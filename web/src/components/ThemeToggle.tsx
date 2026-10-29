import { THEME_LABEL, THEME_NEXT, useTheme, type ThemeChoice } from "../lib/theme";

function Icon({ choice }: { choice: ThemeChoice }) {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (choice === "light") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (choice === "dark") {
    return (
      <svg {...common}>
        <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ThemeToggle() {
  const [choice, setChoice] = useTheme();
  const label = `화면 밝기: ${THEME_LABEL[choice]}`;
  return (
    <button
      type="button"
      className="btn ghost sm theme-toggle"
      aria-label={label}
      title={`${label} (누르면 ${THEME_LABEL[THEME_NEXT[choice]]})`}
      data-theme-choice={choice}
      onClick={() => setChoice(THEME_NEXT[choice])}
    >
      <Icon choice={choice} />
    </button>
  );
}
