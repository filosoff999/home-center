# Governance Home Center

Home Center является самостоятельным продуктом и ведётся только в этом репозитории.

## Изменения

- feature/security/deployment changes проходят через отдельную ветку и Pull Request;
- GitHub-hosted CI обязателен;
- security-critical и production изменения требуют deterministic gates и независимого review, когда reviewer доступен;
- release identity связывает commit, artifact SHA-256 и production evidence;
- direct changes в другие продуктовые репозитории не входят в workflow Home Center.

## Запреты

- cross-product code/runtime/build/deploy dependencies;
- generic remote shell или root-equivalent product API;
- plaintext secrets в repository, Actions logs, issues или evidence;
- automatic failover до witness/fencing certification;
- implicit Samba AD/DNS/DHCP mutation.
