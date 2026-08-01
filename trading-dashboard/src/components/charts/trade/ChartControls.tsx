import { Crosshair, Eye, EyeOff, Ruler, RotateCcw } from 'lucide-react'
import { Button } from '../../ui/Button'

interface ChartControlsProps {
  showMarkers: boolean
  onToggleMarkers: () => void
  autoScale: boolean
  onToggleAutoScale: () => void
  onCenter: () => void
  onReset: () => void
}

/** View toggles and navigation shortcuts for the trade chart. */
export function ChartControls({
  showMarkers,
  onToggleMarkers,
  autoScale,
  onToggleAutoScale,
  onCenter,
  onReset,
}: ChartControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Button
        variant="ghost"
        className="!px-2 !py-1.5 text-[11px]"
        onClick={onToggleMarkers}
        title={showMarkers ? 'Markierungen ausblenden' : 'Markierungen einblenden'}
      >
        {showMarkers ? <EyeOff size={13} /> : <Eye size={13} />}
        <span className="hidden lg:inline">Markierungen</span>
      </Button>

      <Button
        variant="ghost"
        className={[
          '!px-2 !py-1.5 text-[11px]',
          autoScale ? 'text-[var(--color-accent)]' : '',
        ].join(' ')}
        onClick={onToggleAutoScale}
        title={autoScale ? 'Auto Scale deaktivieren' : 'Auto Scale aktivieren'}
        aria-pressed={autoScale}
      >
        <Ruler size={13} />
        <span className="hidden lg:inline">Auto Scale</span>
      </Button>

      <Button
        variant="ghost"
        className="!px-2 !py-1.5 text-[11px]"
        onClick={onCenter}
        title="Auf Trade zentrieren (Doppelklick im Chart)"
      >
        <Crosshair size={13} />
        <span className="hidden lg:inline">Zentrieren</span>
      </Button>

      <Button
        variant="ghost"
        className="!px-2 !py-1.5 text-[11px]"
        onClick={onReset}
        title="Alle geladenen Kerzen einpassen"
      >
        <RotateCcw size={13} />
        <span className="hidden lg:inline">Reset View</span>
      </Button>
    </div>
  )
}
