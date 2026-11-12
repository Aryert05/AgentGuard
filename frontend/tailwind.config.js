/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#0B0F17',
        surface: {
          DEFAULT: '#111827',
          elevated: '#1A2333',
          border: '#232F45',
        },
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        status: {
          success: '#10B981',
          failure: '#EF4444',
          warning: '#F59E0B',
          running: '#3B82F6',
          neutral: '#6B7280',
        }
      },
    },
  },
  plugins: [],
}
