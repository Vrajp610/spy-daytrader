/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        terminal: {
          950: '#070a14',
          900: '#0c1020',
          800: '#151b2e',
          700: '#1e2640',
          600: '#2a3454',
          500: '#3d4a6b',
          400: '#556285',
          300: '#7a89a8',
          200: '#a3afc8',
          100: '#e8eaf0',
        },
        profit: {
          DEFAULT: '#22c55e',
          dim: '#16a34a',
          muted: '#166534',
          glow: '#4ade80',
          bg: 'rgba(34,197,94,0.08)',
        },
        loss: {
          DEFAULT: '#ef4444',
          dim: '#dc2626',
          muted: '#991b1b',
          glow: '#f87171',
          bg: 'rgba(239,68,68,0.08)',
        },
        accent: {
          DEFAULT: '#448aff',
          dim: '#2979ff',
          muted: '#1565c0',
          glow: '#82b1ff',
          bg: 'rgba(68,138,255,0.08)',
        },
        caution: {
          DEFAULT: '#f59e0b',
          dim: '#d97706',
          muted: '#92400e',
          glow: '#fbbf24',
          bg: 'rgba(245,158,11,0.08)',
        },
        muted: '#6b7394',
        subtle: '#4a5578',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'monospace'],
        display: ['Inter Tight', 'Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        xxs: ['0.625rem', { lineHeight: '0.875rem' }],
        xs: ['0.6875rem', { lineHeight: '1rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        lg: ['1rem', { lineHeight: '1.5rem' }],
        xl: ['1.125rem', { lineHeight: '1.625rem' }],
        '2xl': ['1.5rem', { lineHeight: '2rem' }],
        '3xl': ['2rem', { lineHeight: '2.5rem' }],
      },
      borderRadius: {
        card: '0.625rem',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.03)',
        'card-hover': '0 4px 12px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05)',
        'glow-green': '0 0 20px rgba(34,197,94,0.15)',
        'glow-red': '0 0 20px rgba(239,68,68,0.15)',
        'glow-blue': '0 0 20px rgba(68,138,255,0.15)',
        'inset-top': 'inset 0 1px 0 rgba(255,255,255,0.04)',
        'inner-glow': 'inset 0 0 30px rgba(68,138,255,0.03)',
      },
      backgroundImage: {
        'card-gradient': 'linear-gradient(180deg, rgba(255,255,255,0.02) 0%, transparent 100%)',
        'header-gradient': 'linear-gradient(180deg, rgba(12,16,32,0.95) 0%, rgba(7,10,20,0.9) 100%)',
        'surface-noise': 'url("data:image/svg+xml,%3Csvg viewBox=\'0 0 256 256\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23n)\' opacity=\'0.02\'/%3E%3C/svg%3E")',
      },
      keyframes: {
        'pulse-slow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(-4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-down': {
          from: { opacity: '0', transform: 'translateY(-8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'glow-pulse': {
          '0%, 100%': { boxShadow: '0 0 8px rgba(34,197,94,0.3)' },
          '50%': { boxShadow: '0 0 16px rgba(34,197,94,0.5)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'number-tick': {
          '0%': { transform: 'translateY(-100%)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
      animation: {
        'pulse-slow': 'pulse-slow 3s ease-in-out infinite',
        'fade-in': 'fade-in 0.2s ease-out',
        'slide-down': 'slide-down 0.3s ease-out',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
        shimmer: 'shimmer 2s linear infinite',
        'number-tick': 'number-tick 0.3s ease-out',
      },
    },
  },
  plugins: [],
};
