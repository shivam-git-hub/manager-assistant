/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Brand + structural tokens (runtime/semantic colors live in
        // src/constants.ts -- see the note there).
        nav: "#09090b", // Zinc-950 (Vercel/Linear minimalist black)
        navdeep: "#18181b", // Zinc-900
        wordmark: "#ffffff", // White wordmark
        surface: "#f4f4f5", // Zinc-100 page ground
        card: "#ffffff", // Pure white for cards/panels
        cardline: "#e4e4e7", // Zinc-200 border (extremely clean and subtle)
        ink: "#09090b", // Zinc-950 primary text
        inksoft: "#71717a", // Zinc-500 secondary text
        sidebarbg: "#09090b", // Zinc-950 sidebar
        sidebarbtn: "#27272a", // Zinc-800 button
      },
      fontFamily: {
        sans: [
          '"Segoe UI"',
          '"Segoe UI Variable Text"',
          "system-ui",
          "-apple-system",
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
