/**
 * Plain JS for the same reason as vite.config.js: no build step to read config.
 *
 * Design language: restrained developer-tool aesthetic (Linear / Vercel /
 * GitHub). Colours resolve through CSS variables so light and dark themes share
 * one palette definition instead of duplicating every utility class.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: 'rgb(var(--bg-canvas) / <alpha-value>)',
        surface: 'rgb(var(--bg-surface) / <alpha-value>)',
        raised: 'rgb(var(--bg-raised) / <alpha-value>)',
        sunken: 'rgb(var(--bg-sunken) / <alpha-value>)',
        hairline: 'rgb(var(--border-subtle) / <alpha-value>)',
        edge: 'rgb(var(--border-strong) / <alpha-value>)',
        ink: {
          DEFAULT: 'rgb(var(--text-primary) / <alpha-value>)',
          soft: 'rgb(var(--text-secondary) / <alpha-value>)',
          faint: 'rgb(var(--text-tertiary) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          soft: 'rgb(var(--accent-soft) / <alpha-value>)',
        },
        positive: 'rgb(var(--positive) / <alpha-value>)',
        caution: 'rgb(var(--caution) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
        info: 'rgb(var(--info) / <alpha-value>)',
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Microsoft YaHei',
          'Hiragino Sans GB',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Consolas', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: {
        card: '0.625rem',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(var(--shadow-color) / 0.05)',
        pop: '0 8px 24px -8px rgb(var(--shadow-color) / 0.25), 0 2px 6px -2px rgb(var(--shadow-color) / 0.12)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(3px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-ring': {
          '0%': { transform: 'scale(0.85)', opacity: '0.7' },
          '70%': { transform: 'scale(1.6)', opacity: '0' },
          '100%': { transform: 'scale(1.6)', opacity: '0' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 160ms ease-out',
        'pulse-ring': 'pulse-ring 1.6s cubic-bezier(0.24, 0, 0.38, 1) infinite',
        blink: 'blink 1s step-end infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
