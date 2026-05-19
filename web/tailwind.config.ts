import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg:        "hsl(var(--bg))",
        surface:   "hsl(var(--surface))",
        elevated:  "hsl(var(--elevated))",
        border:    "hsl(var(--border))",
        muted:     "hsl(var(--muted))",
        fg:        "hsl(var(--fg))",
        fgMuted:   "hsl(var(--fg-muted))",
        accent:    "hsl(var(--accent))",
        accentFg:  "hsl(var(--accent-fg))",
        rec:       "hsl(var(--rec))",
        success:   "hsl(var(--success))",
        warn:      "hsl(var(--warn))",
        danger:    "hsl(var(--danger))",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
