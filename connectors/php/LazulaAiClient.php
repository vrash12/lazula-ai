<?php
declare(strict_types=1);

/**
 * LAZULA.ai client for plain PHP apps (SermonPost, Media Team). PHP 8.1+, ext-curl.
 * See INTEGRATION_GUIDE.md §3.2. Signing must match app/core/signing.py exactly;
 * tests/test_php_connector.py checks that.
 */
final class LazulaAiException extends RuntimeException
{
    public function __construct(
        public readonly string $errorCode,
        string $message,
        public readonly ?string $requestId = null,
    ) {
        parent::__construct($message);
    }
}

final class LazulaAiClient
{
    private const RETRYABLE_HTTP = [429, 502, 503, 504];

    public function __construct(
        private readonly string $baseUrl,
        private readonly string $app,
        private readonly string $secret,
        private readonly int $timeoutSeconds = 30,
    ) {}

    /** Run a task. Returns the decoded TaskResult array. */
    public function runTask(string $task, string $tenant, array $input, array $options = [], ?string $requestId = null): array
    {
        $body = json_encode(
            ['task' => $task, 'tenant' => $tenant, 'input' => $input, 'options' => (object) $options],
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
        );
        return $this->send('POST', '/v1/tasks', $body, $requestId ?? self::newRequestId());
    }

    public function getTask(string $id): array
    {
        return $this->send('GET', '/v1/tasks/' . rawurlencode($id), '', self::newRequestId());
    }

    /** Register or update a tenant (church, client or team). */
    public function upsertTenant(string $tenant, string $name, string $status = 'active'): array
    {
        $body = json_encode(['name' => $name, 'status' => $status], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        return $this->send('PUT', '/v1/tenants/' . rawurlencode($tenant), $body, self::newRequestId());
    }

    /** The signature the hub expects: HMAC-SHA256 hex over the five lines below. */
    public static function signature(string $secret, string $method, string $pathWithQuery, string $timestamp, string $requestId, string $body): string
    {
        $message = implode("\n", [strtoupper($method), $pathWithQuery, $timestamp, $requestId, hash('sha256', $body)]);
        return hash_hmac('sha256', $message, $secret);
    }

    /** Verify a webhook from LAZULA.ai. Pass headers with lower-cased keys and the raw body. */
    public function verifyWebhook(array $headers, string $rawBody, int $maxSkew = 300): bool
    {
        $ts = $headers['x-lazula-timestamp'] ?? '';
        $rid = $headers['x-lazula-request-id'] ?? '';
        $sig = $headers['x-lazula-signature'] ?? '';
        $path = $headers['x-lazula-path'] ?? '';
        if (!ctype_digit($ts) || abs(time() - (int) $ts) > $maxSkew || $rid === '' || $sig === '' || $path === '') {
            return false;
        }
        return hash_equals(self::signature($this->secret, 'POST', $path, $ts, $rid, $rawBody), $sig);
    }

    private function send(string $method, string $path, string $body, string $requestId): array
    {
        $attempt = 0;
        while (true) {
            $attempt++;
            $ts = (string) time();
            $ch = curl_init(rtrim($this->baseUrl, '/') . $path);
            $opts = [
                CURLOPT_CUSTOMREQUEST => $method,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_CONNECTTIMEOUT => 5,
                CURLOPT_TIMEOUT => $this->timeoutSeconds,
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_SSL_VERIFYHOST => 2,
                CURLOPT_HTTPHEADER => [
                    'Content-Type: application/json',
                    'Accept: application/json',
                    'X-Lazula-App: ' . $this->app,
                    'X-Lazula-Timestamp: ' . $ts,
                    'X-Lazula-Request-Id: ' . $requestId,
                    'X-Lazula-Signature: ' . self::signature($this->secret, $method, $path, $ts, $requestId, $body),
                ],
            ];
            if ($body !== '') {
                $opts[CURLOPT_POSTFIELDS] = $body;
            }
            curl_setopt_array($ch, $opts);
            $raw = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            $netError = $raw === false ? curl_error($ch) : null;
            curl_close($ch);

            $retryable = $netError !== null || in_array($status, self::RETRYABLE_HTTP, true);
            if ($retryable && $attempt < 3) {
                // Same request ID on retry: the hub never runs or bills it twice.
                usleep((int) ((2 ** $attempt) * 250_000 + random_int(0, 250_000)));
                continue;
            }
            if ($netError !== null) {
                throw new LazulaAiException('ai_unavailable', 'Could not reach LAZULA.ai', $requestId);
            }
            $data = json_decode((string) $raw, true);
            if ($status >= 400 || !is_array($data)) {
                $err = is_array($data) ? ($data['error'] ?? []) : [];
                throw new LazulaAiException($err['code'] ?? 'internal_error', $err['message'] ?? "HTTP $status", $err['request_id'] ?? $requestId);
            }
            return $data;
        }
    }

    public static function newRequestId(): string
    {
        return 'rq_' . bin2hex(random_bytes(16));
    }
}
