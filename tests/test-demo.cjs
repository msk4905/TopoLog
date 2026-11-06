const { chromium } = require(process.env.TOPOLOG_PLAYWRIGHT_MODULE || 'playwright');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
(async () => {
  const directory = path.resolve(process.argv[2]);
  const destination = path.resolve(process.argv[3]);
  const browser = await chromium.launch({ headless: true, executablePath: process.argv[4] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const offline = process.argv[5] === 'file';
    if (offline) await page.goto(pathToFileURL(path.join(directory, 'index.html')).href);
    else {
      // Serve the static files through browser test interception. No external
      // server or remote deployment is involved in this HTTP behavior test.
      await page.route('https://topolog.test/**', async route => {
        const pathname = decodeURIComponent(new URL(route.request().url()).pathname);
        const file = path.resolve(directory, '.' + (pathname === '/' ? '/index.html' : pathname));
        if (!file.startsWith(directory + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: 'Not found' });
        return route.fulfill({ path: file });
      });
      await page.goto('https://topolog.test/');
    }
    await page.locator('#topology-image').evaluate(image => image.decode());
    await page.getByRole('link', { name: /실제 검증 자료 보기/ }).click();
    await page.waitForFunction(() => location.hash === '#workspace');
    await page.screenshot({ path: path.join(destination, 'demo-desktop.png'), fullPage: true });
    await page.getByRole('tab', { name: '명령 출력' }).click();
    await page.getByRole('button', { name: 'show ip ospf neighbor', exact: true }).click();
    await page.getByRole('button', { name: '텍스트 보기', exact: true }).click();
    if (!await page.locator('#command-text').isVisible()) throw new Error('Text view did not open');
    if (!(await page.locator('#command-text').textContent()).includes('FULL/')) throw new Error('Actual OSPF output missing');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '원문 내려받기', exact: false }).click();
    const file = await download;
    if (!file.suggestedFilename().endsWith('.txt')) throw new Error('Raw output download failed');
    await page.getByRole('tab', { name: '파일과 활용' }).click();
    const promptDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: 'AI 편집 프롬프트 내려받기', exact: false }).click();
    if ((await promptDownload).suggestedFilename() !== 'ai_prompt.md') throw new Error('Prompt download failed');
    await page.getByRole('tab', { name: '토폴로지', exact: true }).click();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Overflow at ${width}px`);
    }
    await page.screenshot({ path: path.join(destination, 'demo-mobile.png'), fullPage: true });
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(`PASS (${offline ? 'offline' : 'HTTP fixtures'}): demo tabs, command selection, actual output text, downloads and responsive width`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
