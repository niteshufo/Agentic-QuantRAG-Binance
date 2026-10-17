/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        charcoal: {
          main: '#121316',
          surface: '#1C1D22',
          sidebar: '#16171B',
        },
        gold: {
          amber: '#E5A93C',
          dark: '#D48C2A',
        },
      },
    },
  },
  plugins: [],
}
