-- Create enums
CREATE TYPE "PositionSide" AS ENUM ('LONG', 'SHORT');
CREATE TYPE "PositionStatus" AS ENUM ('OPEN', 'CLOSED', 'LIQUIDATED');
CREATE TYPE "TradeSide" AS ENUM ('BUY', 'SELL');
CREATE TYPE "TradeStatus" AS ENUM ('PENDING', 'FILLED', 'CANCELLED', 'REJECTED');
CREATE TYPE "BacktestStatus" AS ENUM ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED');
CREATE TYPE "ConnectionStatus" AS ENUM ('ACTIVE', 'INACTIVE', 'ERROR');
CREATE TYPE "ActivityType" AS ENUM ('LOGIN', 'TRADE_CREATE', 'TRADE_CLOSE', 'BACKTEST_RUN', 'CONFIG_UPDATE', 'API_KEY_UPDATE', 'SYSTEM_EVENT');

-- Create tables
CREATE TABLE "User" (
    "id" TEXT NOT NULL,
    "username" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "hashedPassword" TEXT NOT NULL,
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "Portfolio" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "baseCurrency" TEXT NOT NULL DEFAULT 'USD',
    "isDefault" BOOLEAN NOT NULL DEFAULT false,
    "cashBalance" DECIMAL(18,6) NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "Portfolio_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "Position" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "portfolioId" TEXT NOT NULL,
    "symbol" TEXT NOT NULL,
    "side" "PositionSide" NOT NULL,
    "status" "PositionStatus" NOT NULL DEFAULT 'OPEN',
    "quantity" DECIMAL(20,8) NOT NULL,
    "entryPrice" DECIMAL(20,8) NOT NULL,
    "markPrice" DECIMAL(20,8),
    "stopLoss" DECIMAL(20,8),
    "takeProfit" DECIMAL(20,8),
    "leverage" DECIMAL(10,4),
    "openedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "closedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "Position_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "StrategyConfig" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "portfolioId" TEXT,
    "name" TEXT NOT NULL,
    "symbol" TEXT,
    "timeframe" TEXT,
    "mode" TEXT,
    "isEnabled" BOOLEAN NOT NULL DEFAULT true,
    "riskPerTrade" DECIMAL(10,4),
    "maxLeverage" DECIMAL(10,4),
    "params" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "StrategyConfig_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "Trade" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "portfolioId" TEXT NOT NULL,
    "positionId" TEXT,
    "strategyConfigId" TEXT,
    "symbol" TEXT NOT NULL,
    "side" "TradeSide" NOT NULL,
    "status" "TradeStatus" NOT NULL DEFAULT 'PENDING',
    "orderType" TEXT NOT NULL DEFAULT 'market',
    "quantity" DECIMAL(20,8) NOT NULL,
    "price" DECIMAL(20,8),
    "filledQuantity" DECIMAL(20,8) NOT NULL DEFAULT 0,
    "fee" DECIMAL(20,8),
    "pnl" DECIMAL(20,8),
    "executedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "Trade_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "BacktestResult" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "portfolioId" TEXT,
    "strategyConfigId" TEXT,
    "symbol" TEXT NOT NULL,
    "timeframe" TEXT NOT NULL,
    "startDate" TIMESTAMP(3) NOT NULL,
    "endDate" TIMESTAMP(3) NOT NULL,
    "status" "BacktestStatus" NOT NULL DEFAULT 'COMPLETED',
    "initialCapital" DECIMAL(18,6) NOT NULL,
    "finalValue" DECIMAL(18,6) NOT NULL,
    "totalReturn" DECIMAL(10,4),
    "sharpeRatio" DECIMAL(10,4),
    "sortinoRatio" DECIMAL(10,4),
    "maxDrawdown" DECIMAL(10,4),
    "winRate" DECIMAL(10,4),
    "totalTrades" INTEGER,
    "metadata" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "BacktestResult_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "ApiKeyConnection" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "label" TEXT,
    "apiKeyMasked" TEXT,
    "apiKeyEncrypted" TEXT NOT NULL,
    "apiSecretEncrypted" TEXT NOT NULL,
    "passphraseEncrypted" TEXT,
    "status" "ConnectionStatus" NOT NULL DEFAULT 'ACTIVE',
    "lastCheckedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "ApiKeyConnection_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "ActivityLog" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "portfolioId" TEXT,
    "positionId" TEXT,
    "tradeId" TEXT,
    "apiKeyConnectionId" TEXT,
    "activityType" "ActivityType" NOT NULL,
    "message" TEXT NOT NULL,
    "metadata" JSONB,
    "ipAddress" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ActivityLog_pkey" PRIMARY KEY ("id")
);

-- Create uniques
CREATE UNIQUE INDEX "User_username_key" ON "User"("username");
CREATE UNIQUE INDEX "User_email_key" ON "User"("email");
CREATE UNIQUE INDEX "Portfolio_userId_name_key" ON "Portfolio"("userId", "name");
CREATE UNIQUE INDEX "StrategyConfig_userId_name_key" ON "StrategyConfig"("userId", "name");
CREATE UNIQUE INDEX "ApiKeyConnection_userId_provider_label_key" ON "ApiKeyConnection"("userId", "provider", "label");

-- Create indexes
CREATE INDEX "User_createdAt_idx" ON "User"("createdAt");
CREATE INDEX "Portfolio_userId_idx" ON "Portfolio"("userId");
CREATE INDEX "Portfolio_createdAt_idx" ON "Portfolio"("createdAt");
CREATE INDEX "Position_userId_status_idx" ON "Position"("userId", "status");
CREATE INDEX "Position_portfolioId_symbol_idx" ON "Position"("portfolioId", "symbol");
CREATE INDEX "Position_symbol_idx" ON "Position"("symbol");
CREATE INDEX "Position_openedAt_idx" ON "Position"("openedAt");
CREATE INDEX "Trade_userId_createdAt_idx" ON "Trade"("userId", "createdAt");
CREATE INDEX "Trade_portfolioId_symbol_createdAt_idx" ON "Trade"("portfolioId", "symbol", "createdAt");
CREATE INDEX "Trade_positionId_idx" ON "Trade"("positionId");
CREATE INDEX "Trade_strategyConfigId_idx" ON "Trade"("strategyConfigId");
CREATE INDEX "StrategyConfig_userId_isEnabled_idx" ON "StrategyConfig"("userId", "isEnabled");
CREATE INDEX "StrategyConfig_portfolioId_idx" ON "StrategyConfig"("portfolioId");
CREATE INDEX "BacktestResult_userId_createdAt_idx" ON "BacktestResult"("userId", "createdAt");
CREATE INDEX "BacktestResult_strategyConfigId_idx" ON "BacktestResult"("strategyConfigId");
CREATE INDEX "BacktestResult_portfolioId_idx" ON "BacktestResult"("portfolioId");
CREATE INDEX "BacktestResult_symbol_timeframe_idx" ON "BacktestResult"("symbol", "timeframe");
CREATE INDEX "ApiKeyConnection_userId_status_idx" ON "ApiKeyConnection"("userId", "status");
CREATE INDEX "ApiKeyConnection_provider_idx" ON "ApiKeyConnection"("provider");
CREATE INDEX "ActivityLog_userId_createdAt_idx" ON "ActivityLog"("userId", "createdAt");
CREATE INDEX "ActivityLog_activityType_createdAt_idx" ON "ActivityLog"("activityType", "createdAt");
CREATE INDEX "ActivityLog_portfolioId_idx" ON "ActivityLog"("portfolioId");
CREATE INDEX "ActivityLog_positionId_idx" ON "ActivityLog"("positionId");
CREATE INDEX "ActivityLog_tradeId_idx" ON "ActivityLog"("tradeId");
CREATE INDEX "ActivityLog_apiKeyConnectionId_idx" ON "ActivityLog"("apiKeyConnectionId");

-- Add foreign keys
ALTER TABLE "Portfolio"
    ADD CONSTRAINT "Portfolio_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "Position"
    ADD CONSTRAINT "Position_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "Position"
    ADD CONSTRAINT "Position_portfolioId_fkey"
    FOREIGN KEY ("portfolioId") REFERENCES "Portfolio"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "Trade"
    ADD CONSTRAINT "Trade_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "Trade"
    ADD CONSTRAINT "Trade_portfolioId_fkey"
    FOREIGN KEY ("portfolioId") REFERENCES "Portfolio"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "Trade"
    ADD CONSTRAINT "Trade_positionId_fkey"
    FOREIGN KEY ("positionId") REFERENCES "Position"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "Trade"
    ADD CONSTRAINT "Trade_strategyConfigId_fkey"
    FOREIGN KEY ("strategyConfigId") REFERENCES "StrategyConfig"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "StrategyConfig"
    ADD CONSTRAINT "StrategyConfig_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "StrategyConfig"
    ADD CONSTRAINT "StrategyConfig_portfolioId_fkey"
    FOREIGN KEY ("portfolioId") REFERENCES "Portfolio"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "BacktestResult"
    ADD CONSTRAINT "BacktestResult_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "BacktestResult"
    ADD CONSTRAINT "BacktestResult_portfolioId_fkey"
    FOREIGN KEY ("portfolioId") REFERENCES "Portfolio"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "BacktestResult"
    ADD CONSTRAINT "BacktestResult_strategyConfigId_fkey"
    FOREIGN KEY ("strategyConfigId") REFERENCES "StrategyConfig"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "ApiKeyConnection"
    ADD CONSTRAINT "ApiKeyConnection_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "ActivityLog"
    ADD CONSTRAINT "ActivityLog_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "ActivityLog"
    ADD CONSTRAINT "ActivityLog_portfolioId_fkey"
    FOREIGN KEY ("portfolioId") REFERENCES "Portfolio"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "ActivityLog"
    ADD CONSTRAINT "ActivityLog_positionId_fkey"
    FOREIGN KEY ("positionId") REFERENCES "Position"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "ActivityLog"
    ADD CONSTRAINT "ActivityLog_tradeId_fkey"
    FOREIGN KEY ("tradeId") REFERENCES "Trade"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "ActivityLog"
    ADD CONSTRAINT "ActivityLog_apiKeyConnectionId_fkey"
    FOREIGN KEY ("apiKeyConnectionId") REFERENCES "ApiKeyConnection"("id") ON DELETE SET NULL ON UPDATE CASCADE;
