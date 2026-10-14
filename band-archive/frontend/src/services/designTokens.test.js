import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const css = await readFile(new URL('../index.css', import.meta.url), 'utf8');
const workspaceCss = await readFile(new URL('../components/workspace/BandWorkspace.css', import.meta.url), 'utf8');
const managementCss = await readFile(new URL('../components/bands/BandsPage.css', import.meta.url), 'utf8');
const landingCss = await readFile(new URL('../components/home/WelcomeLanding.css', import.meta.url), 'utf8');
const accountCss = await readFile(new URL('../components/bands/BandsPage.css', import.meta.url), 'utf8');
const dashboardCss = await readFile(new URL('../components/dashboard/BandActivityDashboard.css', import.meta.url), 'utf8');
const rootBlock = css.match(/:root\s*\{([^}]*)\}/s)?.[1] || '';
const token = (name) => rootBlock.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`))?.[1];
const linear = (channel) => { const value = channel / 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4; };
const luminance = (hex) => {
  assert.match(hex, /^#[0-9a-fA-F]{6}$/);
  const [red, green, blue] = hex.slice(1).match(/../g).map((part) => linear(Number.parseInt(part, 16)));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
};
const contrast = (first, second) => {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
};

test('semantic default palette keeps essential text and action pairs above WCAG AA contrast', () => {
  const pairs = [
    ['color-background', 'color-text'],
    ['color-background', 'color-text-muted'],
    ['color-surface', 'color-text'],
    ['color-surface-raised', 'color-text-muted'],
    ['color-surface', 'color-danger'],
    ['color-surface', 'color-success'],
    ['color-accent', 'color-on-accent'],
  ];
  for (const pair of pairs) {
    const [foregroundName, backgroundName] = pair;
    const foreground = token(foregroundName); const background = token(backgroundName);
    assert.ok(foreground && background, `tokens exist for ${foregroundName} on ${backgroundName}`);
    assert.ok(contrast(foreground, background) >= 4.5, `${foregroundName} on ${backgroundName} must be at least 4.5:1`);
  }
});

test('focus token exists and band token aliases are scoped to the workspace, not the global root', () => {
  assert.ok(token('focus-ring-color'));
  assert.match(css, /:where\(a, button, input, select, textarea, \[tabindex\]\):focus-visible\s*\{[^}]*outline: 3px solid var\(--focus-ring-color\)/s);
  assert.doesNotMatch(rootBlock, /--band-theme|--theme-key/);
  assert.match(workspaceCss, /\.band-workspace\s*\{[^}]*--primary-color:\s*var\(--color-accent\)/s);
  assert.doesNotMatch(workspaceCss, /document\.documentElement|\.style\.setProperty/);
});

test('management return link retains a mobile-sized target and long description wrapping', () => {
  assert.match(managementCss, /\.bands-return-link\s*\{[^}]*min-height:\s*44px/s);
  assert.match(managementCss, /@media\s*\(max-width:\s*700px\)/);
  assert.match(managementCss, /\.bands-return-link\s*\{\s*max-width:\s*100%/);
  assert.match(managementCss, /manage-profile-title\s*\+\s*p\s*\{[^}]*white-space:\s*pre-wrap/s);
  assert.match(managementCss, /manage-profile-title\s*\+\s*p\s*\{[^}]*overflow-wrap:\s*anywhere/s);
});

test('active landing, account and workspace styles consume shared semantic tokens without local palettes', () => {
  for (const [name, stylesheet] of [['landing', landingCss], ['account', accountCss], ['workspace', workspaceCss], ['dashboard', dashboardCss]]) {
    assert.doesNotMatch(stylesheet, /#[0-9a-fA-F]{3,8}/, `${name} stylesheet uses shared colors rather than a local hex palette`);
    assert.doesNotMatch(stylesheet, /--welcome-(?:ink|muted|bg|panel|accent)/, `${name} stylesheet has no landing-only color aliases`);
  }
  assert.match(landingCss, /\.welcome-page\s*\{[^}]*var\(--color-background\)/s);
  assert.match(accountCss, /\.bands-shell\s*\{[^}]*var\(--color-background\)/s);
  assert.match(workspaceCss, /\.workspace-error\s*\{\s*color:\s*var\(--accent-color\)/);
  assert.match(dashboardCss, /\.activity-inline-error,\.activity-error\s*\{\s*color:\s*var\(--accent-color\)/);
  assert.match(dashboardCss, /\.react-calendar__tile--active\s*\{[^}]*color:\s*var\(--text-on-primary\)/s);
});

test('color-mix decorations retain semantic background and border fallbacks', () => {
  assert.match(landingCss, /\.welcome-page\s*\{[^}]*background-color:\s*var\(--color-background\);\s*background:\s*radial-gradient/s);
  assert.match(landingCss, /\.welcome-header\s*\{[^}]*border-bottom:\s*1px solid var\(--color-border\);\s*border-bottom-color:\s*color-mix/s);
  assert.match(accountCss, /\.account-avatar\s*\{[^}]*border:\s*1px solid var\(--color-accent\);[^}]*border-color:\s*color-mix/s);
  assert.match(accountCss, /\.account-avatar\s*\{[^}]*background:\s*var\(--color-surface-raised\);\s*background:\s*color-mix/s);
  assert.match(workspaceCss, /\.workspace-header nav\s*\{\s*border-top:\s*1px solid var\(--border-color\);\s*border-top-color:\s*color-mix/s);
});
