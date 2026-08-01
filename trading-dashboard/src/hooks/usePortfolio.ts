import { useDeskData } from '../context/DeskDataContext'

/** Live portfolio + equity from `/api/v1/desk/snapshot`. */
export function usePortfolio() {
  const { portfolio, equity, loading, error } = useDeskData()

  return {
    portfolio,
    equity,
    loading,
    error,
  }
}
