import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

// A small "?" next to a setting: the field itself keeps a one-line hint, the details open on click.
// The card is fixed to the window and rendered outside the form: the panel it belongs to clips its content,
// and a click inside a label would otherwise press the label's own field.
export function InfoHint({ title, children }: { title: string; children: ReactNode }) {
  const [place, setPlace] = useState<{ top: number; left: number; width: number; maxHeight: number }>();
  const button = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = !!place;
  const close = () => setPlace(undefined);
  const toggle = () => {
    if (open || !button.current) return close();
    const rect = button.current.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 24);
    const below = window.innerHeight - rect.bottom - 20, above = rect.top - 20;
    const underneath = below >= 200 || below >= above;
    const maxHeight = Math.max(120, underneath ? below : above);
    setPlace({ width, maxHeight, left: Math.min(Math.max(12, rect.left - 12), window.innerWidth - width - 12), top: underneath ? rect.bottom + 8 : Math.max(12, rect.top - 8 - Math.min(maxHeight, card.current?.offsetHeight ?? maxHeight)) });
  };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!card.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) close(); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); close(); button.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape, true); window.removeEventListener('resize', close); window.removeEventListener('scroll', close, true); };
  }, [open]);
  return <>
    <button type="button" ref={button} className="info-button" aria-label={`Подробнее: ${title}`} aria-expanded={open} aria-controls={open ? id : undefined} onClick={toggle}>?</button>
    {place && createPortal(<div ref={card} id={id} role="region" aria-label={title} className="info-card" style={{ top: place.top, left: place.left, width: place.width, maxHeight: place.maxHeight }}>
      <strong>{title}</strong>{children}
    </div>, document.body)}
  </>;
}
