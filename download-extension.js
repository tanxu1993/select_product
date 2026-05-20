/**
 * 下载上品帮 Chrome 扩展 (.crx)
 * 扩展 ID: ffnehecempjlbkejkmmdeenbodnafjdj
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const EXTENSION_ID = 'ffnehecempjlbkejkmmdeenbodnafjdj';
const OUTPUT_DIR = path.join(__dirname, 'extensions');
const OUTPUT_FILE = path.join(OUTPUT_DIR, `${EXTENSION_ID}.crx`);

// Chrome Web Store 下载 URL
const DOWNLOAD_URL = `https://clients2.google.com/service/update2/crx?response=redirect&prodversion=120.0.0.0&acceptformat=crx3&x=id%3D${EXTENSION_ID}%26uc`;

if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

function download(url, dest, redirectCount = 0) {
  if (redirectCount > 5) {
    console.error('重定向次数过多');
    process.exit(1);
  }

  const client = url.startsWith('https') ? https : require('http');
  const file = fs.createWriteStream(dest);

  client.get(url, (res) => {
    if (res.statusCode === 301 || res.statusCode === 302) {
      file.close();
      fs.unlinkSync(dest);
      console.log(`重定向到: ${res.headers.location}`);
      download(res.headers.location, dest, redirectCount + 1);
      return;
    }

    if (res.statusCode !== 200) {
      console.error(`下载失败，状态码: ${res.statusCode}`);
      process.exit(1);
    }

    res.pipe(file);
    file.on('finish', () => {
      file.close();
      const stats = fs.statSync(dest);
      console.log(`✅ 扩展下载完成: ${dest} (${(stats.size / 1024).toFixed(1)} KB)`);
    });
  }).on('error', (err) => {
    fs.unlink(dest, () => {});
    console.error('下载出错:', err.message);
    process.exit(1);
  });
}

console.log('正在下载上品帮扩展...');
console.log(`扩展 ID: ${EXTENSION_ID}`);
download(DOWNLOAD_URL, OUTPUT_FILE);
