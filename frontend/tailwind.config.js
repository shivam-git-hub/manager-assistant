/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Brand + structural tokens (runtime/semantic colors live in
        // src/constants.ts -- see the note there).
        nav: "#2E77DD", // top bar (wireframe header blue)
        navdeep: "#1F5FC0", // hover/active on the bar
        wordmark: "#BFE6FB", // PULSE wordmark on blue
        surface: "#F4F6FA", // page ground
        card: "#E7F2FA", // panel/card tint (calmed from the wireframe's #A5D8E6)
        cardline: "#C2DCEC", // card border
        ink: "#1E2A38", // primary text
        inksoft: "#5A6B7E", // secondary text
        sidebarbg: "#5EA5F0", // slide-out panel (wireframe 9)
        sidebarbtn: "#2450B5", // pill buttons in the panel
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
