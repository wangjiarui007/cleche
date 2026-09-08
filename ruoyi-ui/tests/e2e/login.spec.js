const { test, expect } = require('@playwright/test')
const fs = require('fs')
const path = require('path')

const envFile = process.env.PLAYWRIGHT_BASE_URL ? '.env.production' : '.env.development'
const platformTitle = fs.readFileSync(path.join(__dirname, '..', '..', envFile), 'utf8')
  .split(/\r?\n/)
  .map(line => line.match(/^\s*VUE_APP_TITLE\s*=\s*(.+?)\s*$/))
  .filter(Boolean)
  .pop()[1]

test('login page renders the production platform identity', async ({ page }) => {
  await page.goto('/login')
  await expect(page).toHaveTitle(platformTitle)
  await expect(page.locator('.brand-title')).toHaveText(platformTitle)
  await expect(page.locator('.brand-eyebrow')).toHaveText('PHM / CONDITION MONITORING')
  await expect(page.getByPlaceholder('账号')).toBeVisible()
  await expect(page.getByPlaceholder('密码')).toBeVisible()
  await expect(page.getByRole('button', { name: /登\s*录/ })).toBeVisible()
  await expect(page.locator('svg symbol#icon-button')).toHaveCount(1)
  await expect(page.locator('svg use')).toHaveCount(3)
  await expect(page.locator('svg symbol#icon-user')).toHaveAttribute('viewBox', '0 0 130 130')
})

test('login captcha network failure does not open webpack overlay', async ({ page }) => {
  const runtimeErrors = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  await page.route('**/captchaImage', route => route.abort())

  await page.goto('/login')
  await expect(page.getByPlaceholder('账号')).toBeVisible()
  await expect(page.locator('.el-message')).toContainText('后端接口连接异常')
  await expect(page.locator('#webpack-dev-server-client-overlay')).toHaveCount(0)
  expect(runtimeErrors.filter(message => /Network Error|AxiosError/.test(message))).toEqual([])
})

test('login business error is shown instead of silently refreshing captcha', async ({ page }) => {
  await page.route('**/csrf', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, token: 'e2e-csrf-token', headerName: 'X-XSRF-TOKEN' })
  }))
  await page.route('**/captchaImage', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, captchaEnabled: false })
  }))
  await page.route('**/login', route => {
    if (route.request().method() !== 'POST') return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 500, msg: '账号或密码错误' })
    })
  })

  await page.goto('/login')
  await page.getByPlaceholder('账号').fill('admin')
  await page.getByPlaceholder('密码').fill('wrong-password')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page.locator('.el-message')).toContainText('账号或密码错误')
})

test('protected route redirects an anonymous browser to login', async ({ page }) => {
  await page.goto('/monitor/diagnosis')
  await expect(page).toHaveURL(/\/login/)
})

test('first-login password gate opens reset-password tab instead of showing 428 overlay', async ({ page }) => {
  const runtimeErrors = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  await page.route('**/csrf', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200 })
  }))
  await page.route('**/captchaImage', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, captchaEnabled: false })
  }))
  await page.route('**/login', route => {
    if (route.request().method() !== 'POST') {
      return route.continue()
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, msg: '操作成功' })
    })
  })
  await page.route('**/getInfo', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 200,
      passwordChangeRequired: true,
      user: { userId: 1, userName: 'admin', nickName: '管理员', avatar: '' },
      roles: ['admin'],
      permissions: ['*:*:*']
    })
  }))
  await page.route('**/system/user/profile', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 200,
      data: { userName: 'admin', nickName: '管理员' },
      roleGroup: '超级管理员',
    })
  }))

  await page.goto('/login')
  await page.getByPlaceholder('账号').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).toHaveURL(/\/user\/profile/)
  await expect(page.locator('.el-tabs__item.is-active')).toContainText('修改密码')
  await expect(page.locator('body')).not.toContainText('系统接口428异常')
  await expect(page.locator('#webpack-dev-server-client-overlay')).toHaveCount(0)
  expect(runtimeErrors.filter(message => /PASSWORD_CHANGE_REQUIRED|status code 403/.test(message))).toEqual([])
})

test('password change refreshes CSRF token before submitting', async ({ page }) => {
  let updateHeaders
  await page.route('**/csrf', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, headerName: 'X-XSRF-TOKEN', token: 'e2e-csrf-token' })
  }))
  await page.route('**/captchaImage', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, captchaEnabled: false })
  }))
  await page.route('**/login', route => {
    if (route.request().method() !== 'POST') return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, msg: '操作成功' })
    })
  })
  await page.route('**/getInfo', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 200,
      passwordChangeRequired: true,
      user: { userId: 1, userName: 'admin', nickName: '管理员', avatar: '' },
      roles: ['admin'],
      permissions: ['*:*:*']
    })
  }))
  await page.route('**/system/user/profile', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200, data: { userName: 'admin', nickName: '管理员' } })
  }))
  await page.route('**/system/user/profile/updatePwd', route => {
    updateHeaders = route.request().headers()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, msg: '操作成功' })
    })
  })
  await page.route('**/logout', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: 200 })
  }))

  await page.goto('/login')
  await page.getByPlaceholder('账号').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).toHaveURL(/\/user\/profile/)
  await page.getByRole('button', { name: '去修改' }).click()
  await page.getByPlaceholder('旧密码').fill('admin123')
  await page.getByPlaceholder('请输入新密码').fill('Newpass123!')
  await page.getByPlaceholder('请确认新密码').fill('Newpass123!')
  await page.getByRole('button', { name: '保存' }).click()
  await expect.poll(() => updateHeaders && updateHeaders['x-xsrf-token']).toBe('e2e-csrf-token')
})

test('authenticated PHM smoke path', async ({ page }) => {
  test.skip(!process.env.PHM_E2E_USER || !process.env.PHM_E2E_PASSWORD,
    'Set PHM_E2E_USER and PHM_E2E_PASSWORD for the full environment smoke test')
  await page.goto('/login')
  await page.getByPlaceholder('账号').fill(process.env.PHM_E2E_USER)
  await page.getByPlaceholder('密码').fill(process.env.PHM_E2E_PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).not.toHaveURL(/\/login/)
  await page.goto('/analysis-toolkit/bearing-diagnosis')
  await expect(page.locator('body')).toContainText('测点诊断总览')
  await expect(page.getByPlaceholder('搜索部门、设备或测点')).toBeVisible()

  const pointCards = page.locator('.point-card')
  if (await pointCards.count()) {
    await pointCards.first().click()
    await expect(page).toHaveURL(/view=detail/)
    await expect(page.locator('body')).toContainText('诊断上下文')
    await expect(page.getByRole('button', { name: /测点总览/ })).toBeVisible()
    await page.getByRole('button', { name: /测点总览/ }).click()
    await expect(page.locator('body')).toContainText('测点诊断总览')
  } else {
    await expect(page.locator('body')).toContainText('暂无可诊断的振动测点')
  }
})

test('authenticated oil monitoring reserved page', async ({ page }) => {
  test.skip(!process.env.PHM_E2E_USER || !process.env.PHM_E2E_PASSWORD,
    'Set PHM_E2E_USER and PHM_E2E_PASSWORD for the full environment smoke test')
  await page.goto('/login')
  await page.getByPlaceholder('账号').fill(process.env.PHM_E2E_USER)
  await page.getByPlaceholder('密码').fill(process.env.PHM_E2E_PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).not.toHaveURL(/\/login/)

  await page.goto('/monitoring-center/oil')
  await expect(page.locator('body')).toContainText('在线油液监测')
  await expect(page.locator('body')).toContainText('油液数据服务尚未接入')
  await expect(page.locator('body')).toContainText('污染度')
  await expect(page.locator('body')).toContainText('铁磁颗粒')
  await expect(page.locator('body')).toContainText('设备状态')

  await page.getByRole('tab', { name: '历史趋势' }).click()
  await expect(page.locator('body')).toContainText('历史接口尚未启用')
  await expect(page.getByText('24小时', { exact: true })).toBeVisible()
  await expect(page.getByText('监测指标', { exact: true })).toBeVisible()
})
