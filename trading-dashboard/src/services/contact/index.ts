import type { ContactMessage } from '../../types/contact'

/**
 * Contract for delivering a contact message.
 * Implementations can later post to SMTP, a Discord webhook, a Telegram bot
 * or a REST endpoint without touching the form components.
 */
export interface ContactTransport {
  readonly id: string
  send(message: ContactMessage): Promise<void>
}

/** Prototype transport: pretends to deliver, resolves after a short delay. */
export const mockContactTransport: ContactTransport = {
  id: 'mock',
  async send(message) {
    await new Promise((resolve) => setTimeout(resolve, 900))
    console.info('[contact] mock delivery', message)
  },
}

/** Swap point once a real channel is configured. */
export const activeContactTransport: ContactTransport = mockContactTransport
