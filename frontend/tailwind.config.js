/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'Liberation Mono', 'monospace'],
      },
      keyframes: {
        // Animation Toast : glisse depuis la droite + fade in.
        // Volontairement courte (120ms) pour rester sobre.
        fadeInRight: {
          '0%':   { opacity: '0', transform: 'translateX(8px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
      },
      animation: {
        fadeInRight: 'fadeInRight 120ms ease-out',
      },
      colors: {
        // DA finale : slate-900 sidebar + jaune electrique.
        steel: {
          // Jaune vif (CTA, focus, etat actif). Echelle proche de Tailwind yellow.
          50:  '#fefce8',
          100: '#fef9c3',
          200: '#fef08a',
          300: '#fde047',
          400: '#facc15',  // accent vif sur fond sombre
          500: '#eab308',
          600: '#ca8a04',  // accent principal sur fond clair
          700: '#a16207',
          800: '#854d0e',
          900: '#713f12',
        },
      },
    },
  },
  plugins: [],
}
