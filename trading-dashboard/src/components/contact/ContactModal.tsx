import { useId, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { activeContactTransport } from '../../services/contact'
import type { ContactMessage } from '../../types/contact'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { ContactForm } from './ContactForm'
import { SuccessMessage } from './SuccessMessage'

interface ContactModalProps {
  open: boolean
  onClose: () => void
}

type Phase = 'form' | 'sending' | 'success'

/** Contact dialog — validation and delivery are wired through a transport. */
export function ContactModal({ open, onClose }: ContactModalProps) {
  const formId = useId()
  const [phase, setPhase] = useState<Phase>('form')

  const close = () => {
    onClose()
    // Reset after the closing animation so the form is clean next time.
    setTimeout(() => setPhase('form'), 200)
  }

  const handleSubmit = async (message: ContactMessage) => {
    setPhase('sending')
    await activeContactTransport.send(message)
    setPhase('success')
  }

  return (
    <Modal
      open={open}
      size="lg"
      title="Kontakt aufnehmen"
      subtitle={
        phase === 'success'
          ? undefined
          : 'Du hast Fragen, Verbesserungsvorschläge oder einen Bug gefunden? Dann sende uns gerne eine Nachricht.'
      }
      onClose={close}
      footer={
        phase === 'success' ? (
          <Button variant="primary" onClick={close}>
            Schließen
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={close} disabled={phase === 'sending'}>
              Abbrechen
            </Button>
            <Button
              variant="primary"
              type="submit"
              form={formId}
              disabled={phase === 'sending'}
              className="min-w-[168px]"
            >
              {phase === 'sending' ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Wird gesendet …
                </>
              ) : (
                'Nachricht senden'
              )}
            </Button>
          </>
        )
      }
    >
      {phase === 'success' ? (
        <SuccessMessage />
      ) : (
        <ContactForm
          formId={formId}
          onSubmit={handleSubmit}
          disabled={phase === 'sending'}
        />
      )}
    </Modal>
  )
}
