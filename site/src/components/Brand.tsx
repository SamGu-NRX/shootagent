/** The product's mark: viewfinder corners around the agent's violet dot. */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg viewBox="0 0 20 20" width={size} height={size} aria-hidden>
      <path d="M2 7V2h5M18 7V2h-5M2 13v5h5M18 13v5h-5" stroke="currentColor" strokeWidth="1.8" fill="none" />
      <circle cx="10" cy="10" r="2.4" fill="var(--agent)" />
    </svg>
  );
}
