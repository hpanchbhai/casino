/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
  extend: {
    colors: {
      background: "#0d1117", // deep dark
      panel: "#161b22",      // card backgrounds
      accent: "#00c2ff",     // cyan highlight
      positive: "#16c784",   // bright green for gains
      negative: "#ea3943",   // vivid red for losses
      neutral: "#8b949e",    // gray text
    },
  },
},

  plugins: [],
};

