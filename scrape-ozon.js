/**
 * Ozon 选品爬虫 v3 - 精准版
 * 完整利用上品帮插件数据（月销量/增速/退货率/发货模式/重量/跟卖者等）
 */
const { chromium } = require('playwright');
const XLSX = require('xlsx');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');

const EXTENSION_DIR = path.join(__dirname, 'extensions', 'unpacked');
const USER_DATA_DIR = path.join(__dirname, 'browser-profile');
const KEYWORD = 'Виброхвост';
const TARGET_PRODUCTS = 2000;
const OUTPUT_FILE = path.join(__dirname, `选品_${KEYWORD.replace(/\s+/g,'_')}_${new Date().toISOString().slice(0,10)}.xlsx`);
const IMAGE_DIR = path.join(__dirname, 'product_images');

// ── 下载图片 ──────────────────────────────────────────────
function downloadImage(url, dest) {
  return new Promise((resolve, reject) => {
    const proto = url.startsWith('https') ? https : http;
    const file = fs.createWriteStream(dest);
    const req = proto.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': 'https://www.ozon.ru/',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
      },
    }, res => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close();
        fs.unlinkSync(dest);
        return downloadImage(res.headers.location, dest).then(resolve).catch(reject);
      }
      if (res.statusCode !== 200) {
        file.close();
        fs.unlinkSync(dest);
        return reject(new Error(`HTTP ${res.statusCode}`));
      }
      res.pipe(file);
      file.on('finish', () => file.close(resolve));
    });
    req.on('error', err => { fs.unlink(dest, () => {}); reject(err); });
    req.setTimeout(10000, () => { req.destroy(); reject(new Error('timeout')); });
  });
}

// ── 解析插件文本数字 ─────────────────────────────────────
function parseNum(val) {
  if (!val || ['无数据', '-', '无跟卖'].includes(val.trim())) return null;
  const m = val.match(/[+-]?[\d.]+/);
  return m ? parseFloat(m[0]) : null;
}

// ── 利润估算 ──────────────────────────────────────────────
function profitCalc(price, weight) {
  if (!price) return { maxCost: null, shipping: null, tier: null };
  const w = weight || 300;
  const tier = price <= 1500 ? 'Extra Small(≤500g)' : 'Small(≤2000g)';
  const shipping = price <= 1500 ? 3 + 0.035 * w : 16 + 0.035 * w;
  // profit = price*0.8 - cost - shipping ≥ price*0.25 => cost ≤ price*0.55 - shipping
  const maxCost = Math.round(price * 0.55 - shipping);
  return { maxCost, shipping: Math.round(shipping * 10) / 10, tier };
}

// ── 选品红线 + 建议评级 ───────────────────────────────────
function evaluate(p) {
  const fails = [];
  const warns = [];

  // 红线
  if (!p.price || p.price < 500 || p.price > 7000)
    fails.push(`价格${p.price ?? '未知'}₽ 需500-7000₽`);
  if (p.shippingMode === 'FBO')
    fails.push('发货模式FBO');
  if (p.sellers !== null && p.sellers > 50)
    fails.push(`跟卖者${p.sellers}个 >50`);
  if (p.returnRate !== null && p.returnRate > 20)
    fails.push(`退货率${p.returnRate}% >20%`);
  if (p.growthRate !== null && p.growthRate < 0)
    fails.push('月销售动态负增长');
  if (p.monthlySales !== null && p.monthlySales < 200)
    fails.push(`月销${p.monthlySales}件 <200`);
  if (p.listedDays !== null && p.listedDays < 180)
    warns.push(`上架仅${p.listedDays}天(<180天)`);
  if (p.price && p.weight) {
    const maxW = p.price <= 1500 ? 500 : 2000;
    if (p.weight > maxW) fails.push(`重量${p.weight}g >${maxW}g限制`);
  }

  // 利润率检查（需要成本，这里用"最大成本"反算）
  const { maxCost, shipping, tier } = profitCalc(p.price, p.weight);
  if (maxCost !== null && maxCost <= 0)
    fails.push('价格过低，利润率无法达25%');

  // 黄金标准评分
  let score = 0;
  if (p.price && p.price >= 600 && p.price <= 5000) score += 2;
  if (p.monthlySales && p.monthlySales >= 300 && p.monthlySales <= 800) score += 3;
  else if (p.monthlySales && p.monthlySales >= 200) score += 1;
  if (p.growthRate && p.growthRate >= 20) score += 3;
  else if (p.growthRate && p.growthRate >= 15) score += 1;
  if (p.sellers !== null && p.sellers >= 5 && p.sellers <= 15) score += 2;
  if (p.returnRate !== null && p.returnRate < 8) score += 2;
  if (p.conversionRate !== null && p.conversionRate > 80) score += 1;
  if (p.ctr !== null && p.ctr >= 3 && p.ctr <= 6) score += 1;
  if (p.promotionDays !== null && p.promotionDays < 15) score += 1;
  if (p.listedDays && p.listedDays > 180) score += 1;
  if (p.searchViews && p.searchViews >= 100000) score += 1;
  if (p.rating && p.rating >= 4.5) score += 1;
  if (fails.length === 0) score += 2;

  return { fails, warns, score, maxCost, shipping, tier };
}

// ── 从页面提取所有商品 ────────────────────────────────────
async function extractAll(page) {
  return page.evaluate(() => {
    function parseNum(val) {
      if (!val || ['无数据', '-', '无跟卖'].includes(val.trim())) return null;
      const m = val.match(/[+-]?[\d.]+/);
      return m ? parseFloat(m[0]) : null;
    }

    const seen = new Set();
    const results = [];

    document.querySelectorAll('.tile-root[data-index]').forEach(card => {
      // ── SKU + URL ──
      const linkEl = card.querySelector('a[href*="/product/"]');
      if (!linkEl) return;
      const href = linkEl.getAttribute('href') || '';
      const skuMatch = href.match(/-(\d{5,12})\/?(?:[?#]|$)/);
      if (!skuMatch) return;
      const sku = skuMatch[1];
      if (seen.has(sku)) return;
      seen.add(sku);

      // ── 商品名（取 bq03_5_1 类的 span，Ozon商品名稳定位置）──
      let name = '';
      const nameSpan = card.querySelector('.bq03_5_1-a span.tsBody500Medium, .bq03_5_1-a span');
      if (nameSpan) {
        name = nameSpan.textContent?.trim() || '';
      }
      if (!name || name.length < 3) {
        // 后备：遍历链接找最长非噪声文本
        const nameLinks = card.querySelectorAll('a[href*="/product/"]');
        for (const lk of nameLinks) {
          const txt = lk.textContent?.trim() || '';
          if (txt.length > 5 && !/^Распродажа|^-\d+%|^Осталось|^Завтра/.test(txt)) { name = txt; break; }
        }
      }

      // ── 图片URL ──
      let imageUrl = '';
      const imgEl = card.querySelector('img[src*="ozonstatic.cn"]') ||
                    card.querySelector('img[src*="ozone.ru"]');
      if (imgEl) {
        imageUrl = imgEl.src.replace(/wc\d+/, 'wc1000'); // 取高清版
      }

      // ── 售价（Ozon卡片，排除插件）──
      // tsHeadline500Medium = Ozon 当前售价的稳定 class
      let price = null;
      const mainPriceEl = card.querySelector('span.tsHeadline500Medium');
      if (mainPriceEl) {
        price = parseInt(mainPriceEl.textContent.replace(/[^\d]/g, '')) || null;
      }

      // ── 评分 + 评价数（Ozon卡片）──
      let rating = null, reviews = null;
      // 评分：span包含纯数字1-5
      card.querySelectorAll('span').forEach(sp => {
        if (rating !== null) return;
        const txt = sp.textContent?.trim();
        if (/^[1-5](\.\d)?$/.test(txt)) rating = parseFloat(txt);
      });
      // 评价数：包含"отзыв"
      const rvMatch = card.textContent.match(/(\d[\d\s]*)\s*отзыв/i);
      if (rvMatch) reviews = parseInt(rvMatch[1].replace(/\s/g, ''));

      // ── 上品帮插件数据 ──
      const pluginEl = card.querySelector('.ozon-bang-item');
      const pd = {}; // pluginData
      if (pluginEl) {
        pluginEl.querySelectorAll('li').forEach(li => {
          const text = li.textContent.trim();
          const idx = text.indexOf('：');
          if (idx === -1) return;
          const label = text.slice(0, idx).replace(/\s+/g, '');
          const value = text.slice(idx + 1).trim();
          pd[label] = value;
        });
      }

      const monthlySales   = parseNum(pd['月销量']);
      const dailySales     = parseNum(pd['日销量']);
      const growthRate     = parseNum(pd['月销售动态']);
      const returnRate     = parseNum(pd['退货取消率']);
      const conversionRate = parseNum(pd['成交率']);
      const ctr            = parseNum(pd['点击率']);
      const cartAddRate    = parseNum(pd['商品卡片加购率']);
      const searchViews    = parseNum(pd['搜索和目录浏览量']);
      const adShare        = parseNum(pd['广告份额']);
      const promotionDays  = parseNum(pd['参与促销天数']);
      const weight         = parseNum(pd['包装重量']);
      const shippingMode   = (pd['发货模式'] && !pd['发货模式'].includes('非热销')) ? pd['发货模式'] : null;
      const category       = (pd['类目'] && !pd['类目'].includes('非热销')) ? pd['类目'] : '';
      const brand          = (pd['品牌'] && !pd['品牌'].includes('非热销')) ? pd['品牌'] : '';
      const avgPrice       = parseNum(pd['平均价格']);
      const lowestCompetitor = pd['跟卖最低价'] || null;
      const sellers        = pd['跟卖者'] === '无跟卖' ? 0
                           : pd['跟卖者'] === '无数据' ? null
                           : parseNum(pd['跟卖者']);

      // 上架天数
      let listedDays = null;
      const listedStr = pd['上架时间'] || '';
      const ldm = listedStr.match(/\((\d+)天\)/);
      if (ldm) listedDays = parseInt(ldm[1]);

      results.push({
        sku, name: name.slice(0, 200),
        price, rating, reviews,
        category, brand,
        monthlySales, dailySales, growthRate,
        returnRate, conversionRate, ctr, cartAddRate,
        searchViews, adShare, promotionDays,
        weight, shippingMode, sellers, lowestCompetitor,
        listedDays, avgPrice,
        hasPlugin: !!pluginEl,
        url: href.startsWith('http') ? href : 'https://www.ozon.ru' + href,
        imageUrl,
      });
    });

    return results;
  });
}

// ── 慢速滚动（触发懒加载 + 等插件注入）────────────────────
async function scrollToLoad(page, target) {
  let prev = 0, stale = 0;
  while (true) {
    await page.evaluate(() => window.scrollBy(0, 800));
    await page.waitForTimeout(800);

    const count = await page.evaluate(() =>
      document.querySelectorAll('.tile-root[data-index]').length
    );
    process.stdout.write(`\r  已渲染 ${count} 个商品卡片...`);

    if (count >= target) { console.log(''); break; }
    if (count === prev) { stale++; if (stale >= 6) { console.log(''); break; } }
    else stale = 0;
    prev = count;
  }
  // 额外等待，让插件补全所有卡片的数据
  console.log('  等待插件数据注入...');
  await page.waitForTimeout(4000);
}

// ── 主流程 ────────────────────────────────────────────────
async function main() {
  console.log('启动浏览器...');
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_DIR}`,
      `--load-extension=${EXTENSION_DIR}`,
      '--no-sandbox',
      '--disable-blink-features=AutomationControlled',
    ],
    viewport: { width: 1440, height: 900 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  });

  const page = await context.newPage();
  let products = [];

  // 拦截图片响应，缓存到内存（key = URL）
  const imageCache = new Map();
  page.on('response', async res => {
    try {
      const url = res.url();
      if (!url.includes('ozonstatic.cn') || !url.match(/\.(jpg|jpeg|webp|png)/i)) return;
      if (res.status() !== 200) return;
      const buf = await res.body();
      imageCache.set(url, buf);
    } catch { /* 忽略 */ }
  });

  try {
    const url = `https://www.ozon.ru/search/?text=${encodeURIComponent(KEYWORD)}&from_global=true&sorting=rating`;
    console.log(`搜索: "${KEYWORD}" (按评分排序)`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 40000 });
    await page.waitForSelector('.tile-root[data-index]', { timeout: 20000 });
    await page.waitForTimeout(2000);

    console.log(`\n滚动加载（目标 ${TARGET_PRODUCTS} 个）...`);
    await scrollToLoad(page, TARGET_PRODUCTS);

    console.log('提取商品数据...');
    products = await extractAll(page);

    const withPlugin = products.filter(p => p.hasPlugin).length;
    console.log(`总计: ${products.length} 个，其中 ${withPlugin} 个有插件数据`);

    // ── 保存商品图片（在浏览器关闭前，缓存还在）──────────
    console.log('\n保存商品图片...');
    if (!fs.existsSync(IMAGE_DIR)) fs.mkdirSync(IMAGE_DIR, { recursive: true });

    // 等待所有 response 回调处理完
    await page.waitForTimeout(1000);

    let imgOk = 0, imgFail = 0;
    for (const p of products) {
      if (!p.imageUrl) { imgFail++; continue; }
      const skuDir = path.join(IMAGE_DIR, p.sku);
      if (!fs.existsSync(skuDir)) fs.mkdirSync(skuDir, { recursive: true });
      const dest = path.join(skuDir, '1.jpg');
      if (fs.existsSync(dest)) { imgOk++; continue; }

      // 优先从浏览器拦截的响应缓存里取（wc1000/wc500/wc300 都试）
      const urlsToTry = [
        p.imageUrl,
        p.imageUrl.replace('wc1000', 'wc500'),
        p.imageUrl.replace('wc1000', 'wc300'),
      ];
      let saved = false;
      for (const tryUrl of urlsToTry) {
        if (imageCache.has(tryUrl)) {
          fs.writeFileSync(dest, imageCache.get(tryUrl));
          saved = true;
          break;
        }
      }

      if (saved) {
        imgOk++;
        process.stdout.write(`\r  已保存 ${imgOk} 张...`);
      } else {
        // 缓存未命中，用 page.goto 直接在浏览器里请求（带完整 Cookie）
        try {
          const resp = await page.goto(p.imageUrl, { timeout: 10000 });
          if (resp && resp.status() === 200) {
            const buf = await resp.body();
            fs.writeFileSync(dest, buf);
            imgOk++;
            process.stdout.write(`\r  已下载 ${imgOk} 张...`);
          } else {
            imgFail++;
          }
        } catch {
          imgFail++;
        }
      }
    }
    console.log(`\n  完成：${imgOk} 张成功，${imgFail} 张失败`);
    console.log(`  图片目录: ${IMAGE_DIR}`);

  } finally {
    await context.close();
  }

  if (products.length === 0) {
    console.error('未提取到商品');
    process.exit(1);
  }

  // ── 评估每个商品 ──────────────────────────────────────
  const rows = products.map((p, i) => {
    const { fails, warns, score, maxCost, shipping, tier } = evaluate(p);
    const pass = fails.length === 0;

    return {
      '#': i + 1,
      '结果': pass ? '✅ 通过' : '❌ 未通过',
      '红线原因': fails.join(' | '),
      '注意事项': warns.join(' | '),
      '黄金评分': score,
      'SKU': p.sku,
      '商品链接': p.url,
      '商品名称': p.name,
      '类目': p.category,
      '品牌': p.brand,
      '当前售价(₽)': p.price,
      '平均价格(₽)': p.avgPrice,
      '物流档位': tier,
      '包装重量(g)': p.weight,
      '预估运费(₽)': shipping,
      '最大成本(₽)': maxCost,
      '发货模式': p.shippingMode,
      '上架天数': p.listedDays,
      '月销量(件)': p.monthlySales,
      '日销量(件)': p.dailySales,
      '月增速(%)': p.growthRate,
      '退货取消率(%)': p.returnRate,
      '成交率(%)': p.conversionRate,
      '点击率(%)': p.ctr,
      '加购率(%)': p.cartAddRate,
      '搜索浏览量': p.searchViews,
      '广告份额(%)': p.adShare,
      '促销天数': p.promotionDays,
      '跟卖者数': p.sellers,
      '跟卖最低价': p.lowestCompetitor,
      '评分': p.rating,
      '评价数': p.reviews,
      '有插件数据': p.hasPlugin ? '是' : '否',
    };
  });

  // 排序：通过 > 未通过；同类按黄金评分降序
  rows.sort((a, b) => {
    if (a['结果'] !== b['结果']) return a['结果'].includes('✅') ? -1 : 1;
    return b['黄金评分'] - a['黄金评分'];
  });

  // ── 写 Excel ──────────────────────────────────────────
  const wb = XLSX.utils.book_new();

  const ws = XLSX.utils.json_to_sheet(rows);
  ws['!cols'] = [
    { wch: 4 },  // #
    { wch: 12 }, // 结果
    { wch: 35 }, // 红线原因
    { wch: 25 }, // 注意事项
    { wch: 8 },  // 黄金评分
    { wch: 14 }, // SKU
    { wch: 70 }, // 商品链接
    { wch: 55 }, // 商品名称
    { wch: 30 }, // 类目
    { wch: 15 }, // 品牌
    { wch: 12 }, // 售价
    { wch: 12 }, // 均价
    { wch: 18 }, // 档位
    { wch: 10 }, // 重量
    { wch: 10 }, // 运费
    { wch: 12 }, // 最大成本
    { wch: 10 }, // 发货模式
    { wch: 10 }, // 上架天数
    { wch: 12 }, // 月销量
    { wch: 12 }, // 日销量
    { wch: 10 }, // 月增速
    { wch: 14 }, // 退货取消率
    { wch: 10 }, // 成交率
    { wch: 10 }, // 点击率
    { wch: 10 }, // 加购率
    { wch: 12 }, // 搜索浏览量
    { wch: 12 }, // 广告份额
    { wch: 10 }, // 促销天数
    { wch: 10 }, // 跟卖者数
    { wch: 15 }, // 跟卖最低价
    { wch: 8 },  // 评分
    { wch: 8 },  // 评价数
    { wch: 10 }, // 有插件
  ];
  XLSX.utils.book_append_sheet(wb, ws, '选品结果');

  // 说明表
  const stdRows = [
    ['指标', '理想值', '红线（直接淘汰）', '说明'],
    ['月销量', '300-800件', '<200件', ''],
    ['月增速', '>20%', '负增长', ''],
    ['跟卖者', '5-15个', '>50个', ''],
    ['退货取消率', '<8%', '>20%', ''],
    ['成交率', '>80%', '', ''],
    ['点击率', '3%-6%', '', ''],
    ['加购率', '>8%', '', ''],
    ['促销天数', '<15天/月', '', ''],
    ['广告份额', '<20%', '', ''],
    ['售价', '600-5000₽', '<500₽或>7000₽', ''],
    ['发货模式', 'FBS', 'FBO', ''],
    ['上架时间', '>6个月(>180天)', '', ''],
    ['利润率', '≥25%', '<25%', '售价×80%-成本-运费'],
    ['', '', '', ''],
    ['物流档位', '售价', '重量', '运费公式'],
    ['Extra Small', '≤1500₽', '≤500g', '3+0.035×W(g)'],
    ['Small', '1501-7000₽', '≤2000g', '16+0.035×W(g)'],
  ];
  const wsStd = XLSX.utils.aoa_to_sheet(stdRows);
  wsStd['!cols'] = [{ wch: 14 }, { wch: 16 }, { wch: 20 }, { wch: 30 }];
  XLSX.utils.book_append_sheet(wb, wsStd, '选品标准');

  XLSX.writeFile(wb, OUTPUT_FILE);

  const passed = rows.filter(r => r['结果'].includes('✅')).length;
  console.log(`\n✅ 完成！`);
  console.log(`   总计: ${rows.length} 个商品`);
  console.log(`   通过: ${passed} 个`);
  console.log(`   未通过: ${rows.length - passed} 个`);
  if (passed > 0) {
    console.log('\n通过的商品（按评分）:');
    rows.filter(r => r['结果'].includes('✅')).slice(0, 10).forEach(r => {
      console.log(`  [${r['黄金评分']}分] ${r['SKU']} | ${r['当前售价(₽)']}₽ | 月销${r['月销量(件)']} | ${r['商品名称'].slice(0,40)}`);
    });
  }
  console.log(`\n📊 Excel: ${OUTPUT_FILE}`);
}

main().catch(e => { console.error('❌ 错误:', e.message); process.exit(1); });
