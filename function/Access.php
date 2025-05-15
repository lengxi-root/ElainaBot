<?php
function BOT凭证()
{
    //引进数据
    $appid = $GLOBALS['appid'];
    $Secret = $GLOBALS['secret'];
    $url = "https://bots.qq.com/app/getAppAccessToken";
    $json = Json(["appId" => $appid, "clientSecret" => $Secret]);
    $header = array('Content-Type: application/json');
    return Json取(curl($url, "POST", $header, $json), "access_token");
}

function BOTAPI($Address, $me, $json)
{
    $url = "https://api.sgroup.qq.com" . $Address;
    $header = array("Authorization: QQBot " . BOT凭证(), 'Content-Type: application/json');
    return curl($url, $me, $header, $json);
}
function Json($content)
{
    return json_encode($content, JSON_UNESCAPED_UNICODE);
}
function Json取($json, $path)
{
    $data = json_decode($json, true);
    $keys = explode('/', $path);
    foreach ($keys as $key) {
        if (is_array($data) && array_key_exists($key, $data)) {
            $data = $data[$key];
        } else {
            return "null";
        }
    }
    return $data;
}

function curl($url, $method, $headers, $params)
{
    $url = str_replace(" ", "%20", $url);
    if (is_array($params)) {
        $requestString = http_build_query($params);
    } else {
        $requestString = $params ?: '';
    }
    if (empty($headers)) {
        $headers = array('Content-type: text/json');
    } elseif (!is_array($headers)) {
        parse_str($headers, $headers);
    }
    // setting the curl parameters.
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_VERBOSE, 1);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    // turning off the server and peer verification(TrustManager Concept).
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
    curl_setopt($ch, CURLOPT_POST, 1);
    // setting the POST FIELD to curl
    switch ($method) {
        case "GET":
            curl_setopt($ch, CURLOPT_HTTPGET, 1);
            break;
        case "POST":
            curl_setopt($ch, CURLOPT_POST, 1);
            curl_setopt($ch, CURLOPT_POSTFIELDS, $requestString);
            break;
        case "PUT":
            curl_setopt($ch, CURLOPT_CUSTOMREQUEST, "PUT");
            curl_setopt($ch, CURLOPT_POSTFIELDS, $requestString);
            break;
        case "DELETE":
            curl_setopt($ch, CURLOPT_CUSTOMREQUEST, "DELETE");
            curl_setopt($ch, CURLOPT_POSTFIELDS, $requestString);
            break;
    }
    // getting response from server
    $response = curl_exec($ch);

    //close the connection
    curl_close($ch);

    //return the response
    if (stristr($response, 'HTTP 404') || $response == '') {
        return array('Error' => '请求错误');
    }
    return $response;
}