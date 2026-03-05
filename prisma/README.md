# Prisma Setup

This workspace includes:

- Schema: `prisma/schema.prisma`
- Initial migration SQL: `prisma/migrations/20260226_000001_init/migration.sql`

## Prerequisites

- `DATABASE_URL` is set for a PostgreSQL database.
- Node.js is installed.

PowerShell example:

```powershell
$env:DATABASE_URL = "postgresql://user:password@localhost:5432/ai_trading"
```

## Install Prisma CLI (if needed)

```powershell
npm install -D prisma
npm install @prisma/client
```

## Generate Prisma Client

```powershell
npx prisma generate
```

## Apply migrations in development

```powershell
npx prisma migrate dev --name init
```

## Apply existing migrations in non-dev environments

```powershell
npx prisma migrate deploy
```

## Check migration state

```powershell
npx prisma migrate status
```

## Reset database (dev only)

```powershell
npx prisma migrate reset
```

## Open Prisma Studio

```powershell
npx prisma studio
```

## Notes

- The migration folder is pre-seeded with an initial SQL migration.
- If your database already has tables with conflicting names, use a fresh schema/database before running `migrate dev`.
