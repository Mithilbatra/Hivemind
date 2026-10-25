// Hivemind mark: a honeycomb cell (the hive) holding a small node graph (the shared mind).
// Same drawing as app/icon.svg, which Next serves as the favicon.
export function LogoMark({ size = 28, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role={title ? "img" : undefined} aria-hidden={title ? undefined : true}>
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id="hm-honey" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#FCD34D" />
          <stop offset="1" stopColor="#F59E0B" />
        </linearGradient>
      </defs>
      <path d="M32 4 56.2 18v28L32 60 7.8 46V18Z" fill="url(#hm-honey)" stroke="#B45309" strokeWidth="2.5" strokeLinejoin="round" />
      <g stroke="#3B2405" strokeWidth="3" strokeLinecap="round">
        <path d="M32 33V18M32 33l12.5 8M32 33l-12.5 8" />
        <path d="M32 18l12.5 23H19.5Z" fill="none" strokeOpacity=".35" strokeWidth="2" strokeLinejoin="round" />
      </g>
      <g fill="#3B2405">
        <circle cx="32" cy="33" r="5" />
        <circle cx="32" cy="17" r="3.6" />
        <circle cx="45" cy="41.5" r="3.6" />
        <circle cx="19" cy="41.5" r="3.6" />
      </g>
    </svg>
  );
}

export function Wordmark({ size = 26 }: { size?: number }) {
  return (
    <span className="wordmark">
      <LogoMark size={size} />
      <span>Hive<b>mind</b></span>
    </span>
  );
}
