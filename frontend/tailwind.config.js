/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        terminal: {
          950: '#0C0C0C',
          900: '#080808',
          800: '#0A0A0A',
          700: '#141414',
          600: '#2f2f2f',
          500: '#3a3a3a',
          400: '#4a4a4a',
          300: '#6a6a6a',
          200: '#8a8a8a',
          100: '#FFFFFF',
        },
        profit: {
          DEFAULT: '#00FF88',
          dim: '#00CC6A',
          muted: '#005c30',
          glow: '#4dffa8',
          bg: 'rgba(0,255,136,0.08)',
        },
        loss: {
          DEFAULT: '#FF4444',
          dim: '#CC2222',
          muted: '#5c1111',
          glow: '#ff7777',
          bg: 'rgba(255,68,68,0.08)',
        },
        accent: {
          DEFAULT: '#00FF88',
          dim: '#00CC6A',
          muted: '#005c30',
          glow: '#4dffa8',
          bg: 'rgba(0,255,136,0.08)',
        },
        caution: {
          DEFAULT: '#FF8800',
          dim: '#CC6A00',
          muted: '#5c3000',
          glow: '#ffaa44',
          bg: 'rgba(255,136,0,0.08)',
        },
        muted: '#6a6a6a',
        subtle: '#4a4a4a',
      },
      fontFamily: {
        sans: ['JetBrains Mono', 'Menlo', 'Monaco', 'monospace'],
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'monospace'],
        display: ['Space Grotesk', 'system-ui', 'sans-serif'],
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
        card: '0px',
        md: '2px',
        full: '9999px',
      },
      boxShadow: {
        card: '0 0 0 1px rgba(47,47,47,0.8)',
        'card-hover': '0 0 0 1px rgba(0,255,136,0.3)',
        'glow-green': '0 0 20px rgba(0,255,136,0.2)',
        'glow-red': '0 0 20px rgba(255,68,68,0.2)',
        'glow-blue': '0 0 20px rgba(0,255,136,0.2)',
        'inset-top': 'inset 0 1px 0 rgba(255,255,255,0.03)',
        'inner-glow': 'inset 0 0 30px rgba(0,255,136,0.03)',
      },
      backgroundImage: {
        'card-gradient': 'none',
        'header-gradient': 'linear-gradient(180deg, rgba(8,8,8,1) 0%, rgba(12,12,12,1) 100%)',
        'surface-noise': 'none',
      },
      keyframes: {
        'pulse-slow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
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
          '0%, 100%': { boxShadow: '0 0 8px rgba(0,255,136,0.3)' },
          '50%': { boxShadow: '0 0 20px rgba(0,255,136,0.6)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'number-tick': {
          '0%': { transform: 'translateY(-100%)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        'scan-line': {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
      },
      animation: {
        'pulse-slow': 'pulse-slow 3s ease-in-out infinite',
        'fade-in': 'fade-in 0.15s ease-out',
        'slide-down': 'slide-down 0.2s ease-out',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
        shimmer: 'shimmer 2s linear infinite',
        'number-tick': 'number-tick 0.3s ease-out',
      },
      letterSpacing: {
        widest: '0.15em',
        terminal: '0.08em',
      },
    },
  },
  plugins: [],
};
