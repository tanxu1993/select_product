/**
 * 1688 以图搜图 - 从Ozon选品结果中找1688货源
 *
 * 流程：
 * 1. 读取Ozon选品Excel，取通过筛选的商品
 * 2. 访问Ozon商品页，抓取主图URL
 * 3. 用1688图片搜索（上传图片）找相似商品
 * 4. 提取相似商品URL + 相似度
 * 5. 写入新Excel
 *
 * 验证码处理：
 * - 滑块验证码：自动模拟拖动
 * - 点选验证码：暂停等待手动处理
 */

const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());
const XLSX = require('xlsx');
const fs = require('fs');
const path = require('path');

// ── 配置 ──────────────────────────────────────────────────────────────────
// 自动查找最新的选品Excel
function findLatestExcel() {
  const files = fs.readdirSync('.').filter(f => f.startsWith('选品_') && f.endsWith('.xlsx') && !f.startsWith('~$'));
  if (files.length === 0) throw new Error('未找到选品Excel文件，请先运行 scrape-ozon.js');
  files.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files[0];
}

const INPUT_EXCEL  = findLatestExcel();
const OUTPUT_EXCEL = `1688匹配_${new Date().toISOString().slice(0, 10)}.xlsx`;
const IMAGE_DIR    = path.join(__dirname, 'product_images');
const USER_DATA_DIR = path.join(__dirname, 'browser-data-1688');
const MAX_RESULTS_PER_PRODUCT = 5;   // 每个商品最多保存几条1688结果
const REQUEST_DELAY_MS = [4000, 7000]; // 请求间隔随机范围（毫秒）

// ── 工具函数 ──────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function randomDelay() {
  const [min, max] = REQUEST_DELAY_MS;
  return min + Math.random() * (max - min);
}

// ── 验证码处理 ────────────────────────────────────────────────────────────

// 检测页面是否有验证码
async function detectCaptcha(page) {
  return page.evaluate(() => {
    const body = document.body.innerText || '';
    const hasSlider = !!document.querySelector(
      '.nc-container, .nc_wrapper, [id*="nc_"], .baxia-dialog, #baxia-dialog'
    );
    const hasClickCaptcha = !!document.querySelector(
      '.captcha-container, [class*="captcha"], [id*="captcha"]'
    );
    const hasBlockText = body.includes('请完成安全验证') || body.includes('滑动验证') ||
                         body.includes('请按住滑块') || body.includes('人机验证');
    return { hasSlider, hasClickCaptcha, hasBlockText };
  });
}

// 尝试自动过滑块验证码
async function solveSliderCaptcha(page) {
  console.log('  🤖 尝试自动过滑块验证码...');
  try {
    // 等待滑块出现
    const sliderHandle = await page.waitForSelector(
      '.nc_iconfont.btn_slide, .nc-lang-cnt, [class*="nc-lang"], .btn_slide',
      { timeout: 5000 }
    );
    if (!sliderHandle) return false;

    const box = await sliderHandle.boundingBox();
    if (!box) return false;

    // 模拟人类拖动：加速-匀速-减速
    const startX = box.x + box.width / 2;
    const startY = box.y + box.height / 2;
    const targetX = startX + 280; // 1688滑块通常需要拖动约280px

    await page.mouse.move(startX, startY);
    await page.mouse.down();

    // 分段移动，模拟人类行为
    const steps = 30;
    for (let i = 0; i <= steps; i++) {
      const progress = i / steps;
      // 缓动函数：先快后慢
      const eased = progress < 0.5
        ? 2 * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 2) / 2;
      const x = startX + (targetX - startX) * eased;
      const jitter = (Math.random() - 0.5) * 2; // 轻微抖动
      await page.mouse.move(x, startY + jitter);
      await sleep(20 + Math.random() * 30);
    }

    await page.mouse.up();
    await sleep(2000);

    // 检查是否通过
    const stillHasCaptcha = await detectCaptcha(page);
    if (!stillHasCaptcha.hasSlider && !stillHasCaptcha.hasBlockText) {
      console.log('  ✅ 滑块验证码已通过');
      return true;
    }
    console.log('  ❌ 滑块验证码未通过，需要手动处理');
    return false;
  } catch (e) {
    console.log(`  ⚠️  滑块处理异常: ${e.message}`);
    return false;
  }
}

// 等待验证码被手动解决
async function waitForCaptchaResolved(page, timeoutMs = 120000) {
  console.log(`  ⏳ 请在浏览器中手动完成验证码（最多等待 ${timeoutMs / 1000}s）...`);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(2000);
    const captcha = await detectCaptcha(page);
    if (!captcha.hasSlider && !captcha.hasClickCaptcha && !captcha.hasBlockText) {
      console.log('  ✅ 验证码已解决，继续...');
      return true;
    }
  }
  console.log('  ❌ 验证码超时，跳过此商品');
  return false;
}

// 统一验证码处理入口
async function handleCaptcha(page) {
  const captcha = await detectCaptcha(page);
  if (!captcha.hasSlider && !captcha.hasClickCaptcha && !captcha.hasBlockText) {
    return true; // 无验证码
  }

  console.log('  🚨 检测到验证码');

  if (captcha.hasSlider) {
    const solved = await solveSliderCaptcha(page);
    if (solved) return true;
  }

  // 自动处理失败，等待手动
  return waitForCaptchaResolved(page);
}

// ── 获取商品图片路径 ──────────────────────────────────────────────────────
// 优先使用 scrape-ozon.js 已下载的图片（product_images/{SKU}/1.jpg）
// 如果没有则报错，提示先跑 scrape-ozon.js

function getProductImagePath(sku) {
  const skuDir = path.join(IMAGE_DIR, sku);
  const imgPath = path.join(skuDir, '1.jpg');
  if (fs.existsSync(imgPath)) {
    return imgPath;
  }
  // 兼容旧的平铺结构 product_images/{SKU}.jpg
  const flatPath = path.join(IMAGE_DIR, `${sku}.jpg`);
  if (fs.existsSync(flatPath)) {
    return flatPath;
  }
  return null;
}

// ── 1688 以图搜图 ─────────────────────────────────────────────────────────

async function search1688ByImage(page, imagePath) {
  // 1688图片搜索入口
  const searchUrl = 'https://s.1688.com/youyuan/index.htm?tab=imageSearch';
  await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2000);

  // 处理可能的验证码
  const captchaOk = await handleCaptcha(page);
  if (!captchaOk) return [];

  // 找文件上传输入框
  let fileInput = null;
  try {
    fileInput = await page.waitForSelector('input[type="file"]', { timeout: 8000 });
  } catch {
    // 有些版本需要先点击相机图标
    const cameraBtn = page.locator('[class*="camera"], [class*="upload"], .search-image-btn').first();
    if (await cameraBtn.count() > 0) {
      await cameraBtn.click();
      await sleep(1000);
      fileInput = await page.waitForSelector('input[type="file"]', { timeout: 5000 });
    }
  }

  if (!fileInput) {
    throw new Error('未找到图片上传入口');
  }

  // 上传图片
  await fileInput.setInputFiles(path.resolve(imagePath));
  console.log('  📤 图片已上传，点击搜索按钮...');

  // 等待"搜索图片"按钮出现并点击
  await sleep(1500);
  try {
    const searchBtn = await page.waitForSelector(
      '.search-btn[data-tracker="pasteImagePreview"], .search-btn',
      { timeout: 8000 }
    );
    await searchBtn.click();
    console.log('  ✅ 已点击搜索图片按钮');
  } catch {
    console.log('  ⚠️  未找到搜索按钮，尝试继续...');
  }

  // 等待结果页加载
  await sleep(3000);
  await handleCaptcha(page); // 上传后可能触发验证码

  // 等待商品列表出现
  try {
    await page.waitForSelector(
      '.offer-item, .sm-offer-item, [class*="offer-item"], .img-search-result-item',
      { timeout: 15000 }
    );
  } catch {
    console.log('  ⚠️  等待结果超时，尝试提取当前页面内容');
  }

  await sleep(1000);

  // 提取搜索结果
  const results = await page.evaluate((maxResults) => {
    const items = [];

    // 多种可能的卡片选择器
    const cardSelectors = [
      '.offer-item',
      '.sm-offer-item',
      '.img-search-result-item',
      '[class*="offer-item"]',
      '.card-container',
    ];

    let cards = [];
    for (const sel of cardSelectors) {
      cards = Array.from(document.querySelectorAll(sel));
      if (cards.length > 0) break;
    }

    for (let i = 0; i < Math.min(cards.length, maxResults); i++) {
      const card = cards[i];

      // 获取链接
      const linkEl = card.querySelector('a[href*="offer"], a[href*="1688.com"]') ||
                     card.querySelector('a');
      const url = linkEl ? linkEl.href : '';
      if (!url) continue;

      // 获取相似度（多种可能的位置）
      const simEl = card.querySelector(
        '[class*="similar"], [class*="rate"], [class*="match"], .similarity'
      );
      let similarity = simEl ? simEl.innerText.trim() : '';

      // 有些版本相似度在data属性里
      if (!similarity) {
        similarity = card.getAttribute('data-similarity') ||
                     card.getAttribute('data-match-rate') || '';
      }

      // 获取价格
      const priceEl = card.querySelector('[class*="price"], .price');
      const price = priceEl ? priceEl.innerText.trim().replace(/\s+/g, '') : '';

      // 获取标题
      const titleEl = card.querySelector('[class*="title"], .title, h3, h4');
      const title = titleEl ? titleEl.innerText.trim().slice(0, 100) : '';

      items.push({ url, similarity, price, title });
    }

    return items;
  }, MAX_RESULTS_PER_PRODUCT);

  return results;
}

// ── 主流程 ────────────────────────────────────────────────────────────────

(async () => {
  console.log(`📖 读取选品结果: ${INPUT_EXCEL}`);
  const wb = XLSX.readFile(INPUT_EXCEL);
  const ws = wb.Sheets['选品结果'];
  const rows = XLSX.utils.sheet_to_json(ws);

  const passedProducts = rows.filter(r => r['结果'] && r['结果'].includes('✅'));
  console.log(`✅ 找到 ${passedProducts.length} 个通过筛选的商品`);

  if (passedProducts.length === 0) {
    console.log('❌ 没有通过筛选的商品，退出');
    return;
  }

  if (!fs.existsSync(IMAGE_DIR)) fs.mkdirSync(IMAGE_DIR, { recursive: true });

  console.log('🚀 启动浏览器（反检测模式）...');
  const browser = await chromium.launch({
    headless: false,
    args: [
      '--disable-blink-features=AutomationControlled',
      '--disable-dev-shm-usage',
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-features=IsolateOrigins,site-per-process',
      '--window-size=1280,900',
    ],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    storageState: fs.existsSync(path.join(USER_DATA_DIR, 'state.json'))
      ? path.join(USER_DATA_DIR, 'state.json')
      : undefined,
    extraHTTPHeaders: {
      'Accept-Language': 'zh-CN,zh;q=0.9',
    },
  });

  // 注入反检测脚本（每个新页面都会执行）
  await context.addInitScript(() => {
    // 删除 webdriver 标记
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 伪造真实 Chrome 对象
    window.chrome = {
      runtime: {
        connect: () => {},
        sendMessage: () => {},
      },
      loadTimes: () => ({}),
      csi: () => ({}),
      app: { isInstalled: false },
    };

    // 伪造语言
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

    // 伪造插件（空插件列表是自动化特征）
    const mockPlugins = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    Object.defineProperty(navigator, 'plugins', {
      get: () => Object.assign(mockPlugins, { item: i => mockPlugins[i], namedItem: n => mockPlugins.find(p => p.name === n), refresh: () => {} }),
    });

    // 伪造权限
    const origQuery = navigator.permissions && navigator.permissions.query.bind(navigator.permissions);
    if (origQuery) {
      navigator.permissions.query = params =>
        params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : origQuery(params);
    }

    // 修复 iframe contentWindow 检测
    const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      get: function() {
        const win = origContentWindow.get.call(this);
        if (win) {
          Object.defineProperty(win.navigator, 'webdriver', { get: () => undefined });
        }
        return win;
      },
    });
  });

  const page = await context.newPage();
  const outputRows = [];

  // 先访问1688确认登录状态
  console.log('\n🔐 检查1688登录状态...');
  await page.goto('https://www.1688.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2000);

  const isLoggedIn = await page.evaluate(() => {
    return document.body.innerText.includes('我的1688') ||
           document.body.innerText.includes('退出') ||
           !!document.querySelector('[class*="user-name"], [class*="username"]');
  });

  if (!isLoggedIn) {
    console.log('⚠️  未登录1688，请在浏览器中手动登录后按回车继续...');
    await new Promise(resolve => process.stdin.once('data', resolve));
  } else {
    console.log('✅ 已登录1688');
  }

  // 登录后立即保存 cookie
  if (!fs.existsSync(USER_DATA_DIR)) fs.mkdirSync(USER_DATA_DIR, { recursive: true });
  await context.storageState({ path: path.join(USER_DATA_DIR, 'state.json') });
  console.log('💾 Cookie 已保存');

  for (let i = 0; i < passedProducts.length; i++) {
    const product = passedProducts[i];
    const sku = String(product['SKU'] || '');
    const ozonUrl = product['商品链接'] || '';
    const productName = product['商品名称'] || '';
    const ozonPrice = product['当前售价(₽)'] || '';
    const monthlySales = product['月销量(件)'] || '';
    const score = product['黄金评分'] || '';

    console.log(`\n[${i + 1}/${passedProducts.length}] ${productName.slice(0, 50)}`);
    console.log(`  SKU: ${sku} | 售价: ${ozonPrice}₽ | 月销: ${monthlySales}`);

    try {
      // 获取商品图片（使用 scrape-ozon.js 已下载的）
      const imagePath = getProductImagePath(sku);
      if (!imagePath) {
        console.log('  ⚠️  图片不存在，跳过（请先运行 scrape-ozon.js 下载图片）');
        outputRows.push({
          'SKU': sku,
          '商品名称': productName,
          '黄金评分': score,
          'Ozon售价(₽)': ozonPrice,
          'Ozon月销量': monthlySales,
          'Ozon链接': ozonUrl,
          '1688链接': '',
          '1688价格': '',
          '1688标题': '',
          '相似度': '',
          '状态': '图片缺失',
        });
        continue;
      }
      console.log(`  📷 使用图片: ${imagePath}`);

      // 1688以图搜图
      const searchResults = await search1688ByImage(page, imagePath);

      if (searchResults.length === 0) {
        console.log('  ❌ 未找到匹配商品');
        outputRows.push({
          'SKU': sku,
          '商品名称': productName,
          '黄金评分': score,
          'Ozon售价(₽)': ozonPrice,
          'Ozon月销量': monthlySales,
          'Ozon链接': ozonUrl,
          '1688链接': '',
          '1688价格': '',
          '1688标题': '',
          '相似度': '',
          '状态': '无匹配结果',
        });
      } else {
        console.log(`  ✅ 找到 ${searchResults.length} 个相似商品`);
        searchResults.forEach((item, idx) => {
          console.log(`    ${idx + 1}. 相似度:${item.similarity || '未知'} 价格:${item.price} ${item.url.slice(0, 60)}`);
          outputRows.push({
            'SKU': sku,
            '商品名称': productName,
            '黄金评分': score,
            'Ozon售价(₽)': ozonPrice,
            'Ozon月销量': monthlySales,
            'Ozon链接': ozonUrl,
            '1688链接': item.url,
            '1688价格': item.price,
            '1688标题': item.title,
            '相似度': item.similarity,
            '状态': idx === 0 ? '最佳匹配' : `相似商品${idx + 1}`,
          });
        });
      }

    } catch (error) {
      console.error(`  ❌ 处理失败: ${error.message}`);
      outputRows.push({
        'SKU': sku,
        '商品名称': productName,
        '黄金评分': score,
        'Ozon售价(₽)': ozonPrice,
        'Ozon月销量': monthlySales,
        'Ozon链接': ozonUrl,
        '1688链接': '',
        '1688价格': '',
        '1688标题': '',
        '相似度': '',
        '状态': `错误: ${error.message.slice(0, 50)}`,
      });
    }

    // 每处理完一个商品就保存一次（防止中途崩溃丢数据）
    saveExcel(outputRows, OUTPUT_EXCEL);

    // 同时保存 cookie（防止会话过期）
    await context.storageState({ path: path.join(USER_DATA_DIR, 'state.json') });

    // 随机延迟
    if (i < passedProducts.length - 1) {
      const delay = randomDelay();
      console.log(`  ⏱️  等待 ${(delay / 1000).toFixed(1)}s...`);
      await sleep(delay);
    }
  }

  await context.close();
  await browser.close();

  const matched = outputRows.filter(r => r['1688链接']).length;
  console.log(`\n✅ 全部完成！`);
  console.log(`   处理商品: ${passedProducts.length} 个`);
  console.log(`   匹配记录: ${matched} 条`);
  console.log(`   输出文件: ${OUTPUT_EXCEL}`);
})().catch(e => {
  console.error('❌ 致命错误:', e.message);
  process.exit(1);
});

// ── 保存Excel（增量写入）────────────────────────────────────────────────
function saveExcel(rows, filePath) {
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.json_to_sheet(rows);
  ws['!cols'] = [
    { wch: 14 }, // SKU
    { wch: 50 }, // 商品名称
    { wch: 8  }, // 黄金评分
    { wch: 12 }, // Ozon售价
    { wch: 12 }, // Ozon月销量
    { wch: 70 }, // Ozon链接
    { wch: 70 }, // 1688链接
    { wch: 15 }, // 1688价格
    { wch: 60 }, // 1688标题
    { wch: 12 }, // 相似度
    { wch: 20 }, // 状态
  ];
  XLSX.utils.book_append_sheet(wb, ws, '1688匹配结果');
  XLSX.writeFile(wb, filePath);
}
