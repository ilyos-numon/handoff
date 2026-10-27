export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M9 20h8a5 5 0 0 0 0-10h-2" fill="none" stroke="var(--bg)" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="12" cy="10" r="2.4" fill="var(--role-leaver)" />
    </svg>
  );
}
