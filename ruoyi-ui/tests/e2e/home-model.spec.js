const { test, expect } = require('@playwright/test')

test('home keeps device overview actions and adds an interactive 3D device view', async ({ page }) => {
  await page.addInitScript(() => {
    document.cookie = 'Admin-Token=e2e-token; path=/'
  })
  await page.route('**/getInfo', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 200,
      user: { userId: 1, userName: 'admin', nickName: '管理员', avatar: '' },
      roles: ['admin'],
      permissions: ['*:*:*']
    })
  }))
  await page.route('**/getRouters', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, data: [] })
  }))
  await page.route('**/sensor/monitoring/overview', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 200,
      data: {
        summary: { totalDevices: 2, onlineDevices: 2, abnormalDevices: 1, unacknowledgedAlarms: 1, dataDelaySeconds: 2 },
        devices: [
          { id: 1, deviceCode: 'ARM-001', deviceName: '采煤机摇臂 1#', status: 'level3', healthIndex: 72, latestVibration: 6.42, latestTemperature: 58.3 },
          { id: 2, deviceCode: 'ARM-002', deviceName: '采煤机摇臂 2#', status: 'normal', healthIndex: 96, latestVibration: 1.12, latestTemperature: 41.7 }
        ],
        alarms: []
      }
    })
  }))

  await page.goto('/index')

  await expect(page.getByText('设备三维视图')).toBeVisible()
  await expect(page.locator('canvas[aria-label="当前设备三维模型"]')).toBeVisible()
  await expect(page.getByText('采煤机摇臂 1#', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.device-overview-card')).toHaveCount(2)
  await expect(page.getByRole('button', { name: /进入 采煤机摇臂 1# 的实时监测/ })).toBeVisible()

  await page.locator('.device-select .el-input').click()
  await page.getByText('采煤机摇臂 2#', { exact: true }).last().click()
  await expect(page.locator('.model-device-info')).toContainText('正常运行')
  await expect(page.getByRole('button', { name: '复位' })).toBeVisible()
})
