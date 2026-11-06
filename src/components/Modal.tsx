import { useEffect, useId, useRef, type ReactNode } from 'react';

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  return <dialog ref={ref} className="modal" aria-labelledby={id} onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="modal-heading"><h2 id={id}>{title}</h2><button className="modal-close" aria-label="Закрыть" onClick={onClose}>×</button></div>
    {children}
  </dialog>;
}
