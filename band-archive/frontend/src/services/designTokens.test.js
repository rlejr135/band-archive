import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const css = await readFile(new URL('../index.css', import.meta.url), 'utf8');
const workspaceCss = await readFile(new URL('../components/workspace/BandWorkspace.css', import.meta.url), 'utf8');
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
