/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Semantic tokens driven by CSS variables (see src/design-system/tokens.css).
        surface: 'rgb(var(--surface) / <alpha-value>)',
        panel: 'rgb(var(--panel) / <alpha-value>)',
        chip: 'rgb(var(--chip) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        'line-strong': 'rgb(var(--line-strong) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        'ink-strong': 'rgb(var(--ink-strong) / <alpha-value>)',
        'ink-mid': 'rgb(var(--ink-mid) / <alpha-value>)',
        'ink-dim': 'rgb(var(--ink-dim) / <alpha-value>)',
        'ink-faint': 'rgb(var(--ink-faint) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        'accent-hover': 'rgb(var(--accent-hover) / <alpha-value>)',
        'accent-strong': 'rgb(var(--accent-strong) / <alpha-value>)',
        'accent-soft': 'rgb(var(--accent-soft) / <alpha-value>)',
        'btn-ink': 'rgb(var(--btn-ink) / <alpha-value>)',
        'bubble-user': 'rgb(var(--bubble-user) / <alpha-value>)',
        'bubble-user-ink': 'rgb(var(--bubble-user-ink) / <alpha-value>)',
        ok: 'rgb(var(--ok) / <alpha-value>)',
        warn: 'rgb(var(--warn) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
        // Route-badge tokens (see RouteBadge.tsx) — same pattern as every
        // other semantic color, replacing a hardcoded Tailwind palette.
        'route-memory': 'rgb(var(--route-memory) / <alpha-value>)',
        'route-memory-ink': 'rgb(var(--route-memory-ink) / <alpha-value>)',
        'route-rag': 'rgb(var(--route-rag) / <alpha-value>)',
        'route-rag-ink': 'rgb(var(--route-rag-ink) / <alpha-value>)',
        'route-web': 'rgb(var(--route-web) / <alpha-value>)',
        'route-web-ink': 'rgb(var(--route-web-ink) / <alpha-value>)',
        'route-nl2sql': 'rgb(var(--route-nl2sql) / <alpha-value>)',
        'route-nl2sql-ink': 'rgb(var(--route-nl2sql-ink) / <alpha-value>)',
        'route-tools': 'rgb(var(--route-tools) / <alpha-value>)',
        'route-tools-ink': 'rgb(var(--route-tools-ink) / <alpha-value>)',
        // Legacy brand scale (kept for light-theme gradients on the login page).
        brand: {
          50: '#eef6ff',
          100: '#d9eaff',
          500: '#2563eb',
          600: '#1d4ed8',
          700: '#1e40af',
        },
      },
      fontFamily: {
        display: ['Rajdhani', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
