import {
  useEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { Icon, type IconName } from './icons';
import type { Tone } from '../types';

/* Small presentational primitives shared across every screen.
 * Styling lives in index.css; these components only compose it. */

type Div = React.HTMLAttributes<HTMLDivElement>;

interface PanelProps extends Omit<Div, 'title'> {
  title?: ReactNode;
  hint?: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  tight?: boolean;
  footer?: ReactNode;
}

export function Panel({
  title,
  hint,
  icon,
  actions,
  children,
  flush,
  tight,
  footer,
  style,
  ...rest
}: PanelProps) {
  return (
    <section className="panel" style={style} {...rest}>
      {(title || actions) && (
        <header className="panel__head">
          <h2 className="panel__title">
            {icon && <Icon name={icon} size={15} />}
            {typeof title === 'string' ? <span className="panel__title-text">{title}</span> : title}
          </h2>
          {actions && <div className="panel__actions">{actions}</div>}
        </header>
      )}
      {hint && !title && <p className="panel__body panel__body--tight t-faint">{hint}</p>}
      <div className={flush ? 'panel__body panel__body--flush' : tight ? 'panel__body panel__body--tight' : 'panel__body'}>
        {children}
      </div>
      {footer && <div className="panel__foot">{footer}</div>}
    </section>
  );
}

export function Badge({
  tone = 'idle',
  children,
  icon,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  icon?: IconName;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {icon && <Icon name={icon} size={10} strokeWidth={2} />}
      {children}
    </span>
  );
}

export function Dot({ tone = 'idle', pulse }: { tone?: Tone; pulse?: boolean }) {
  return <span className={`dot dot--${tone}${pulse ? ' dot--pulse' : ''}`} />;
}

export function Rail({ tone = 'idle' }: { tone?: Tone }) {
  return <span className={`rail rail--${tone}`} />;
}

export function Bar({
  value = 0,
  tone,
  indeterminate,
}: {
  value?: number;
  tone?: Tone;
  indeterminate?: boolean;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const fillTone =
    tone ?? (clamped >= 100 ? 'ok' : clamped >= 60 ? 'accent' : clamped > 0 ? 'info' : 'idle');
  const cls =
    fillTone === 'accent'
      ? ''
      : fillTone === 'plain'
        ? ''
        : ` bar__fill--${fillTone}`;
  return (
    <div
      className={`bar${indeterminate ? ' bar--indeterminate' : ''}`}
      role="progressbar"
      aria-valuenow={indeterminate ? undefined : clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className={`bar__fill${cls}`} style={indeterminate ? undefined : { width: `${clamped}%` }} />
    </div>
  );
}

export function Button({
  children,
  variant = 'default',
  size,
  icon,
  block,
  ...rest
}: {
  children?: ReactNode;
  variant?: 'default' | 'primary' | 'ghost' | 'subtle' | 'danger' | 'danger-solid';
  size?: 'sm';
  icon?: IconName;
  block?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = [
    'btn',
    variant !== 'default' ? `btn--${variant}` : '',
    size === 'sm' ? 'btn--sm' : '',
    block ? 'btn--block' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button type="button" className={classes} {...rest}>
      {icon && <Icon name={icon} size={size === 'sm' ? 12 : 14} />}
      {children}
    </button>
  );
}

export function IconButton({
  icon,
  label,
  tone,
  ...rest
}: {
  icon: IconName;
  label: string;
  tone?: 'default' | 'ghost' | 'danger';
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`btn btn--icon${tone && tone !== 'default' ? ` btn--${tone}` : ''}`}
      {...rest}
    >
      <Icon name={icon} size={15} />
    </button>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  tone,
  title,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  tone?: 'danger';
  title?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      title={title}
      onClick={() => onChange(!checked)}
      className={`switch${checked ? ' switch--on' : ''}${tone === 'danger' && checked ? ' switch--danger' : ''}`}
    >
      <span className="switch__track">
        <span className="switch__thumb" />
      </span>
      {label && <span className="switch__label">{label}</span>}
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (next: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="segmented" role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={`segmented__item${o.value === value ? ' segmented__item--active' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  ariaLabel?: string;
}) {
  return (
    <div className="search">
      <span className="search__icon">
        <Icon name="search" size={14} />
      </span>
      <input
        className="input"
        type="search"
        value={value}
        aria-label={ariaLabel ?? placeholder}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function Chips<T extends string>({
  value,
  options,
  onChange,
  allLabel = 'All',
}: {
  value: T;
  options: Array<{ value: T; label: string; count?: number }>;
  onChange: (next: T) => void;
  allLabel?: string;
}) {
  return (
    <div className="chips" role="group">
      {allLabel && <button
        type="button"
        className={`chip${value === ('__all__' as T) ? ' chip--active' : ''}`}
        onClick={() => onChange('__all__' as T)}
      >
        {allLabel}
      </button>}
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`chip${value === o.value ? ' chip--active' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {typeof o.count === 'number' && <span className="t-faint"> {o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  icon = 'inbox',
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty__icon">
        <Icon name={icon} size={26} strokeWidth={1.3} />
      </span>
      <p className="empty__title">{title}</p>
      {description && <p className="empty__desc">{description}</p>}
      {action}
    </div>
  );
}

export function Notice({
  tone = 'info',
  icon,
  children,
  action,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'accent';
  icon?: IconName;
  children: ReactNode;
  action?: ReactNode;
}) {
  const fallback: IconName = tone === 'warn' || tone === 'danger' ? 'alert' : tone === 'accent' ? 'info' : 'info';
  return (
    <div className={`notice notice--${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      <span className="notice__icon">
        <Icon name={icon ?? fallback} size={14} />
      </span>
      <div className="notice__body">{children}</div>
      {action}
    </div>
  );
}

/** Marks a panel as demonstration data. Always rendered when data is invented. */
export function DemoTag({ compact }: { compact?: boolean }) {
  return (
    <Badge tone="warn" title="Demonstration data — not read from the Sythion backend.">
      {compact ? 'demo' : 'Demo data'}
    </Badge>
  );
}

export function DataBadge({ origin, label }: { origin: 'live' | 'demo'; label?: string }) {
  if (origin === 'demo') return <DemoTag compact />;
  return <Badge tone="ok" title="Read live from the Sythion backend.">{label ?? 'Live'}</Badge>;
}

export function Skeleton({ height = 14, width = '100%' }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} />;
}

export function LoadingBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack stack--tight" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={i === 0 ? 18 : 13} width={i === 0 ? '55%' : `${88 - i * 9}%`} />
      ))}
    </div>
  );
}

export function Stat({
  label,
  value,
  suffix,
  foot,
  tone,
  icon,
  onClick,
  active,
}: {
  label: string;
  value: ReactNode;
  suffix?: string;
  foot?: ReactNode;
  tone?: Tone;
  icon?: IconName;
  onClick?: () => void;
  active?: boolean;
}) {
  const content = (
    <>
      <span className="stat__label">
        {icon && <Icon name={icon} size={12} />}
        {label}
      </span>
      <span className="stat__value">
        {value}
        {suffix && <span className="stat__suffix">{suffix}</span>}
      </span>
      <span className="stat__foot">{foot}</span>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={`stat stat--clickable${active ? ' stat--active' : ''}${
          tone === 'accent' ? ' stat--accent' : ''
        }`}
        onClick={onClick}
      >
        {content}
      </button>
    );
  }
  return (
    <div className={`stat${tone === 'accent' ? ' stat--accent' : ''}`}>{content}</div>
  );
}

export function KV({ items }: { items: Array<{ k: string; v: ReactNode }> }) {
  return (
    <div className="kv">
      {items.map((it) => (
        <div key={it.k} style={{ display: 'contents' }}>
          <span className="kv__k">{it.k}</span>
          <span className="kv__v">{it.v}</span>
        </div>
      ))}
    </div>
  );
}

export function Drawer({
  title,
  subtitle,
  onClose,
  children,
  footer,
  width,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Details'}
        style={width ? ({ width: `min(${width}px, 92vw)` } as CSSProperties) : undefined}
      >
        <header className="drawer__head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="t-title">{title}</h2>
            {subtitle && <p className="t-faint" style={{ margin: '3px 0 0' }}>{subtitle}</p>}
          </div>
          <IconButton icon="close" label="Close panel" onClick={onClose} tone="ghost" />
        </header>
        <div className="drawer__body">{children}</div>
        {footer && <div className="drawer__foot">{footer}</div>}
      </aside>
    </>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="modal" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : 'Dialog'}>
        <header className="panel__head">
          <h2 className="panel__title">{title}</h2>
          <div className="panel__actions">
            <IconButton icon="close" label="Close dialog" onClick={onClose} tone="ghost" />
          </div>
        </header>
        <div className="panel__body">{children}</div>
        {footer && <div className="drawer__foot">{footer}</div>}
      </div>
    </>
  );
}

export function ConfirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  tone = 'default',
  onConfirm,
  onCancel,
  busy,
}: {
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  tone?: 'default' | 'danger';
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <Button onClick={onCancel} variant="subtle">
            Cancel
          </Button>
          <Button
            onClick={onConfirm}
            variant={tone === 'danger' ? 'danger-solid' : 'primary'}
            disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="t-body">{body}</div>
    </Modal>
  );
}

/** Non-modal popover anchored to a wrapper element. Closes on outside click. */
export function Popover({
  open,
  onClose,
  trigger,
  children,
  align = 'right',
  width,
}: {
  open: boolean;
  onClose: () => void;
  trigger: ReactNode;
  children: ReactNode;
  align?: 'right' | 'left';
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  return (
    <div ref={ref} style={{ position: 'relative', display: 'flex' }}>
      {trigger}
      {open && (
        <div
          className="popover"
          style={{
            ...(align === 'left' ? { right: 'auto', left: 0 } : {}),
            ...(width ? { width: `min(${width}px, 84vw)` } : {}),
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function ScreenHead({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="screen-head">
      <div className="screen-head__text">
        {eyebrow && <div className="screen-head__eyebrow">{eyebrow}</div>}
        <h1 className="screen-head__title">{title}</h1>
        {description && <p className="screen-head__desc">{description}</p>}
      </div>
      {actions && <div className="screen-head__actions">{actions}</div>}
    </header>
  );
}
