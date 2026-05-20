/**
 * 启动浏览器，加载上品帮插件，打开 shopbang.cn 等待手动登录，保存 session
 *
 * 使用方式:
 *   node login-and-save.js
 *
 * 登录完成后按 Enter 键保存 session 到 auth-state.json
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

const EXTENSION_PATH = path.join(__dirname, 'extensions', 'unpacked');
const USER_DATA_DIR = path.join(__dirname, 'browser-profile');
const AUTH_STATE_FILE = path.join(__dirname, 'auth-state.json');
const SHOPBANG_URL = 'https://shopbang.cn/';

async function waitForEnter(prompt) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, () => {
      rl.close();
      resolve();
    });
  });
}

async function main() {
  // 检查扩展目录是否存在
  if (!fs.existsSync(EXTENSION_PATH)) {
    console.error(`❌ 未找到扩展目录: ${EXTENSION_PATH}`);
    console.error('请先运行: node download-extension.js && node unpack-extension.js');
    process.exit(1);
  }

  console.log('🚀 启动 Chrome 浏览器（持久化 profile）...');

  // 使用持久化 context，支持加载 crx 扩展
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
      '--no-sandbox',
      '--disable-blink-features=AutomationControlled',
    ],
    viewport: { width: 1280, height: 800 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  });

  const page = await context.newPage();

  console.log(`📂 浏览器 profile 保存在: ${USER_DATA_DIR}`);
  console.log('🔌 正在加载上品帮插件...');

  // 等待扩展加载
  await page.waitForTimeout(2000);

  console.log(`🌐 打开 ${SHOPBANG_URL} ...`);
  await page.goto(SHOPBANG_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });

  console.log('\n========================================');
  console.log('✋ 请在浏览器中手动完成登录操作');
  console.log('   登录完成后，回到此终端按 Enter 键');
  console.log('========================================\n');

  await waitForEnter('登录完成后按 Enter 保存 session...');

  // 保存 cookies 和 storage state
  const storageState = await context.storageState();
  fs.writeFileSync(AUTH_STATE_FILE, JSON.stringify(storageState, null, 2), 'utf-8');

  console.log(`\n✅ Session 已保存到: ${AUTH_STATE_FILE}`);
  console.log(`   包含 ${storageState.cookies.length} 个 cookies`);
  console.log(`   包含 ${storageState.origins.length} 个 origin 的 localStorage`);

  await context.close();
  console.log('\n🎉 完成！下次可直接使用 auth-state.json 恢复登录状态。');
}

main().catch((err) => {
  console.error('❌ 出错:', err.message);
  process.exit(1);
});
