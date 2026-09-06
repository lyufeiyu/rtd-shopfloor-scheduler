// 产品级 smoke test：只验证用户可见闭环，不读取算法源代码。
const { chromium } = require('/Users/feiyulv/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:8765/');
    await page.locator('#upload').setInputFiles('/Users/feiyulv/Desktop/RTD算法/algorithm/RTD_Dataset_v6.xlsx');
    await page.waitForFunction(() => document.querySelectorAll('#dataset-select option').length > 0, null, { timeout: 30000 });
    await page.locator('[data-view="devices"]').click();
    await page.locator('table tbody tr').first().waitFor();
    assert.match(await page.locator('body').innerText(), /设备台账/);
    await page.locator('[data-view="workspace"]').click();
    await page.locator('#run-station').selectOption('WF');
    assert.equal(await page.locator('#run-station').inputValue(), 'WF');
    await page.locator('#run').click();
    await page.locator('#job-status').waitFor();
    await page.waitForFunction(() => document.querySelector('#job-status')?.innerText.includes('排产完成'), null, { timeout: 30000 });
    await page.locator('[data-view="results"]').click();
    await page.locator('text=已安排批次').waitFor();
    assert.match(await page.locator('body').innerText(), /250/);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ upload: true, devices: true, submitted: true, result: true, browserErrors: errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
