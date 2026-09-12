# Сертификаты

Используйте отдельную browser-compatible TLS identity для Web и отдельную mutual-TLS identity для межузлового взаимодействия.

Web-сертификат должен соответствовать настроенному hostname и management address; peer-сертификат — identity соответствующего узла. Private keys храните вне исходного кода и runtime-архивов, в файлах с root-controlled permissions.

Перед активацией проверяйте certificate chain, имена, алгоритмы, срок действия и соответствие private key сертификату. Предыдущую валидную identity сохраняйте доступной для безопасного rollback.

Истёкшая, неподходящая или непроверяемая TLS identity является блокирующим состоянием; её нельзя обходить отключением проверки в production.
