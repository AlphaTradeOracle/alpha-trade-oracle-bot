SELECT a.symbol, s.direction, s.confidence, s.score, s.data_quality,
       s.reference_price, s.stop_loss, s.take_profit_1, s.risk_reward_ratio,
       s.llm_summary IS NOT NULL AS has_llm,
       LEFT(s.llm_summary, 200) AS llm_preview,
       s.created_at
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.direction IN ('LONG', 'SHORT')
ORDER BY s.score DESC
LIMIT 10;
