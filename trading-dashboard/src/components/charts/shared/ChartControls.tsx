import { Crosshair, Eye, EyeOff, Ruler, RotateCcw } from 'lucide-react'
import { Button } from '../../ui/Button'

interface ToggleConfig {
  active: boolean
  onToggle: () => void
  /** Text shown next to the icon on wide screens */
  label: string
}

interface ChartControlsProps {
  /** Optional overlay/marker visibility toggle */
  markers?: ToggleConfig
  autoScale: Omit<ToggleConfig, 'label'> & { label?: string }
  /** Optional "focus the interesting part" action */
  onCenter?: () => void
  centerLabel?: string
  onReset: () => void
}

const buttonClass = '!px-2 !py-1.5 text-[11px]'

/** View toggles and navigation shortcuts shared by all charts. */
export function ChartControls({
  markers,
  autoScale,
  onCenter,
  centerLabel = 'Zentrieren',
  onReset,
}: ChartControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {markers ? (
        <Button
          variant="ghost"
          className={buttonClass}
          onClick={markers.onToggle}
          title={markers.active ? `${markers.label} ausblenden` : `${markers.label} einblenden`}
          aria-pressed={markers.active}
        >
          {markers.active ? <EyeOff size={13} /> : <Eye size={13} />}
          <span className="hidden lg:inline">{markers.label}</span>
        </Button>
      ) : null}

      <Button
        variant="ghost"
        className={[buttonClass, autoScale.active ? 'text-[var(--color-accent)]' : ''].join(' ')}
        onClick={autoScale.onToggle}
        title={autoScale.active ? 'Auto Scale deaktivieren' : 'Auto Scale aktivieren'}
        aria-pressed={autoScale.active}
      >
        <Ruler size={13} />
        <span className="hidden lg:inline">{autoScale.label ?? 'Auto Scale'}</span>
      </Button>

      {onCenter ? (
        <Button
          variant="ghost"
          className={buttonClass}
          onClick={onCenter}
          title={`${centerLabel} (Doppelklick im Chart)`}
        >
          <Crosshair size={13} />
          <span className="hidden lg:inline">{centerLabel}</span>
        </Button>
      ) : null}

      <Button variant="ghost" className={buttonClass} onClick={onReset} title="Ansicht zurücksetzen">
        <RotateCcw size={13} />
        <span className="hidden lg:inline">Reset View</span>
      </Button>
    </div>
  )
}
