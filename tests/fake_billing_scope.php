<?php
/**
 * Проверочная «касса сайта» — тот же набор методов, что у vllm/lib/billing.php шлюза (Billing::get(), ready, pdo, mode,
 * settings, wallet, move, charge, check, spentBySid, fmt), на тех же таблицах billing_wallets / billing_ledger, но с
 * простыми ценами. Подключение к базе — как у настоящей кассы (через $db_config сайта). Для site_wallet_scope_test.py.
 */
final class Billing
{
    public const MICRO = 1000000;
    private static ?Billing $inst = null;
    private ?PDO $pdo = null;

    public static function get(): Billing { return self::$inst ??= new Billing(); }

    /** Как касса шлюза (vllm/lib/billing.php): настройки базы — глобальный $db_config; нет его — подгружает
     *  connections/config.php и PostgreSQLDB.php сайта сама, изнутри метода (там глобальные переменные config.php не видны). */
    public function pdo(): PDO
    {
        if ($this->pdo) return $this->pdo;
        global $db_config;
        if (empty($db_config)) {
            $root = dirname(__DIR__, 2);
            require_once $root . '/connections/config.php';
            require_once $root . '/connections/PostgreSQLDB.php';
            if (empty($db_config) && isset($GLOBALS['db_config'])) $db_config = $GLOBALS['db_config'];
        }
        if (empty($db_config) || empty($db_config['database'])) throw new RuntimeException('billing: не найдены настройки базы ($db_config)');
        $c = $db_config;
        $this->pdo = new PDO("pgsql:host={$c['host']};port={$c['port']};dbname={$c['database']}", $c['username'], $c['password'],
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC, PDO::ATTR_TIMEOUT => 5]);
        $this->pdo->exec('SET search_path TO ' . preg_replace('/\W/', '', (string)getenv('BILLING_SCHEMA')) . ', public');
        return $this->pdo;
    }

    public function ready(): bool
    {
        try { return (bool)$this->pdo()->query("SELECT to_regclass('billing_ledger') IS NOT NULL")->fetchColumn(); } catch (Throwable $e) { return false; }
    }

    public function mode(): string { return getenv('FAKE_BILLING_MODE') ?: 'enforce'; }
    public function settings(): array { return ['topup_text' => getenv('FAKE_TOPUP_TEXT') ?: 'Пополнить — в кабинете dewiar.pro, раздел «Касса».']; }

    /** Цены «Кассы»: FAKE_PRICES = {"слот": [вход, выход] в микроталерах за 1 млн токенов, "*chat": по умолчанию} */
    public function priceFor(string $modelId, string $type): array
    {
        $p = json_decode((string)getenv('FAKE_PRICES'), true) ?: [];
        $r = $p[$modelId] ?? $p['*' . $type] ?? [0, 0];
        return ['in_per_m' => (int)$r[0], 'out_per_m' => (int)$r[1], 'per_minute' => 0];
    }

    public function wallet(int $u): array
    {
        $q = $this->pdo()->prepare('SELECT * FROM billing_wallets WHERE user_id = ?');
        $q->execute([$u]);
        $w = $q->fetch();
        if (!$w) {
            $this->pdo()->prepare('INSERT INTO billing_wallets (user_id, balance) VALUES (?, 0)')->execute([$u]);
            return $this->wallet($u);
        }
        return ['user_id' => $u, 'balance' => (int)$w['balance'], 'credit' => (int)$w['credit'], 'plan_id' => null,
                'blocked' => in_array($w['blocked'], [true, 't', 1, '1'], true)];
    }

    public function move(int $u, int $amount, string $kind, array $meta = []): int
    {
        $this->wallet($u);
        $q = $this->pdo()->prepare('UPDATE billing_wallets SET balance = balance + ? WHERE user_id = ? RETURNING balance');
        $q->execute([$amount, $u]);
        $bal = (int)$q->fetchColumn();
        $this->pdo()->prepare('INSERT INTO billing_ledger (user_id, kind, amount, balance_after, app, model, tokens_in, tokens_out, units, request_id, note, sid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')->execute([$u, $kind, $amount, $bal, (string)($meta['app'] ?? ''), (string)($meta['model'] ?? ''),
            (int)($meta['tokens_in'] ?? 0), (int)($meta['tokens_out'] ?? 0), (float)($meta['units'] ?? 0), (string)($meta['request_id'] ?? ''),
            (string)($meta['note'] ?? ''), (string)($meta['sid'] ?? '')]);
        return $bal;
    }

    public function charge(int $u, string $model, string $type, int $in, int $out, array $meta = [], float $units = 0): ?array
    {
        $cost = $in + $out + (int)round($units * 10000);
        $bal = $this->move($u, -$cost, 'charge', $meta + ['model' => $model, 'tokens_in' => $in, 'tokens_out' => $out, 'units' => $units]);
        return ['cost' => $cost, 'balance' => $bal];
    }

    public function check(int $u, string $role = ''): array
    {
        $w = $this->wallet($u);
        if ($w['blocked']) return ['ok' => false, 'status' => 403, 'reason' => 'Доступ к ИИ приостановлен администратором.'];
        if ($w['balance'] + $w['credit'] <= 0 && $this->mode() === 'enforce')
            return ['ok' => false, 'status' => 402, 'reason' => 'На счёте закончились ИИ-талеры. Пополните баланс в личном кабинете.'];
        return ['ok' => true];
    }

    public function spentBySid(int $u, string $sid): array
    {
        $q = $this->pdo()->prepare("SELECT COUNT(*) n, COALESCE(-SUM(amount), 0) c FROM billing_ledger WHERE user_id = ? AND sid = ? AND kind = 'charge'");
        $q->execute([$u, $sid]);
        $r = $q->fetch();
        return ['cost' => (int)$r['c'], 'n' => (int)$r['n'], 'parts' => []];
    }

    public static function fmt(int $micro): string
    {
        return number_format($micro / self::MICRO, 4, ',', ' ') . ' тал.';
    }
}
