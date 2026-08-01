/** Payload the contact form produces — shape is transport-agnostic. */
export interface ContactMessage {
  email: string
  telegram?: string
  message: string
  /** ISO timestamp of submission */
  sentAt: string
}

export interface ContactFormErrors {
  email?: string
  message?: string
}

export const CONTACT_MESSAGE_MAX = 2000
