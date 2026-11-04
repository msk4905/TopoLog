const { chromium } = require(process.env.TOPOLOG_PLAYWRIGHT_MODULE || 'playwright');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const directory = path.resolve(process.argv[2]);
  const destination = path.resolve(process.argv[3]);
  const browser = await chromium.launch({ headless: true, ...(process.argv[4] ? { executablePath: process.argv[4] } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.join(directory, 'index.html')).href);
    await page.locator('img').evaluateAll(images => Promise.all(images.map(image => {
      image.loading = 'eager';
      return image.decode();
    })));
    await page.screenshot({ path: path.join(destination, 'report-desktop.png'), fullPage: true });
    if (errors.length) throw new Error(errors.join('\n'));
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (overflow) throw new Error(`Horizontal overflow at ${width}px`);
    }
    await page.screenshot({ path: path.join(destination, 'report-mobile.png'), fullPage: true });
    console.log('PASS: HTML preview images, no script errors, desktop/mobile page width');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
