const { test, expect } = require('@playwright/test')

test('application exposes the fixed industrial design tokens', async ({ page }) => {
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'industrial')
  await expect(page.locator('.login-stage')).toBeVisible()

  const theme = await page.evaluate(() => {
    const styles = getComputedStyle(document.documentElement)
    const stage = document.querySelector('.login-stage')
    if (!stage) throw new Error('login stage container is missing')
    const stageStyles = getComputedStyle(stage)
    return {
      canvas: styles.getPropertyValue('--color-canvas').trim(),
      surface: styles.getPropertyValue('--color-surface').trim(),
      text: styles.getPropertyValue('--color-text').trim(),
      accent: styles.getPropertyValue('--color-accent').trim(),
      opsAccent: styles.getPropertyValue('--ops-accent').trim(),
      chartText: styles.getPropertyValue('--chart-text').trim(),
      stageRadius: stageStyles.borderRadius
    }
  })

  expect(theme).toEqual({
    canvas: '#0b1220',
    surface: '#121d2c',
    text: '#d9e2ec',
    accent: '#44c7b5',
    opsAccent: '#44c7b5',
    chartText: '#d9e2ec',
    stageRadius: '10px'
  })
})

test('legacy appearance settings cannot override the fixed theme', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('layout-setting', JSON.stringify({
      appearanceMode: 'light',
      sideTheme: 'theme-light',
      theme: '#F59E0B'
    }))
  })
  await page.goto('/login')

  const result = await page.evaluate(() => {
    const styles = getComputedStyle(document.documentElement)
    return {
      mode: document.documentElement.getAttribute('data-theme'),
      accent: styles.getPropertyValue('--color-accent').trim()
    }
  })

  expect(result).toEqual({ mode: 'industrial', accent: '#44c7b5' })
})

test('login remains readable without horizontal overflow on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/login')
  await expect(page.locator('.login-stage')).toBeVisible()

  const layout = await page.evaluate(() => {
    const stage = document.querySelector('.login-stage')
    const form = document.querySelector('.auth-form')
    if (!stage || !form) throw new Error('login layout containers are missing')
    const stageRect = stage.getBoundingClientRect()
    const formRect = form.getBoundingClientRect()
    return {
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      left: Math.round(stageRect.left),
      right: Math.round(innerWidth - stageRect.right),
      formInsideStage: formRect.left >= stageRect.left && formRect.right <= stageRect.right
    }
  })

  expect(layout.overflow).toBe(false)
  expect(layout.left).toBeGreaterThanOrEqual(12)
  expect(layout.right).toBeGreaterThanOrEqual(12)
  expect(layout.formInsideStage).toBe(true)
})
