<?php
//webhook签名校验
class signs{
public function sign($data){
$botSecret =$GLOBALS['secret'];
$payload = json_decode($data, true);
$seed = $botSecret;
while (strlen($seed) < SODIUM_CRYPTO_SIGN_SEEDBYTES) {
    $seed .= $seed;
}
$privateKey = sodium_crypto_sign_secretkey(
    sodium_crypto_sign_seed_keypair(substr($seed, 0, SODIUM_CRYPTO_SIGN_SEEDBYTES))
);
$signature = bin2hex(
    sodium_crypto_sign_detached(
        $payload['d']['event_ts'] . $payload['d']['plain_token'], 
        $privateKey
    )
);
echo json_encode([
    'plain_token' => $payload['d']['plain_token'],
    'signature' => $signature
]);
  }
}