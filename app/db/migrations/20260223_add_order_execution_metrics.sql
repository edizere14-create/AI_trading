-- Add columns for execution quality + partial fills
ALTER TABLE orders ADD COLUMN order_id VARCHAR(100);
ALTER TABLE orders ADD COLUMN order_kind VARCHAR(20) DEFAULT 'taker';
ALTER TABLE orders ADD COLUMN filled_quantity FLOAT DEFAULT 0.0;
ALTER TABLE orders ADD COLUMN avg_fill_price FLOAT NULL;
ALTER TABLE orders ADD COLUMN slippage FLOAT NULL;
ALTER TABLE orders ADD COLUMN fill_rate FLOAT NULL;
ALTER TABLE orders ADD COLUMN latency_ms FLOAT NULL;
ALTER TABLE orders ADD COLUMN filled_at TIMESTAMP NULL;

-- Update enum values if using native enums (PostgreSQL)
-- ALTER TYPE orderstatus ADD VALUE 'partial';