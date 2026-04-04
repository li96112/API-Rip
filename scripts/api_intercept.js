(function() {
  // API-Rip: Intercept all fetch/XHR requests and responses
  // Hooks fetch + XHR for future requests
  // Provides replay() to re-fetch missed requests discovered via Performance API

  if (window.__API_RIP__) {
    return JSON.stringify({
      status: 'already_installed',
      captured: window.__API_RIP__.getCaptureCount(),
    });
  }

  var captures = [];
  var captureId = 0;
  var replayedUrls = {};  // track what we've already replayed

  // ---- Fetch interceptor ----
  var origFetch = window.fetch;
  window.fetch = function() {
    var args = arguments;
    var url = '';
    var method = 'GET';
    var reqHeaders = {};
    var reqBody = null;

    if (typeof args[0] === 'string') {
      url = args[0];
    } else if (args[0] instanceof Request) {
      url = args[0].url;
      method = args[0].method || 'GET';
    }

    if (args[1]) {
      method = args[1].method || method;
      reqHeaders = {};
      if (args[1].headers) {
        if (args[1].headers instanceof Headers) {
          args[1].headers.forEach(function(v, k) { reqHeaders[k] = v; });
        } else {
          reqHeaders = Object.assign({}, args[1].headers);
        }
      }
      reqBody = args[1].body || null;
      if (reqBody && typeof reqBody !== 'string') {
        try { reqBody = JSON.stringify(reqBody); } catch(e) { reqBody = '[non-string body]'; }
      }
    }

    var id = ++captureId;
    var startTime = Date.now();

    return origFetch.apply(this, args).then(function(response) {
      var cloned = response.clone();
      var resHeaders = {};
      cloned.headers.forEach(function(v, k) { resHeaders[k] = v; });

      cloned.text().then(function(body) {
        var contentType = resHeaders['content-type'] || '';
        var isJson = contentType.indexOf('json') !== -1;
        var parsedBody = null;
        if (isJson) {
          try { parsedBody = JSON.parse(body); } catch(e) { parsedBody = null; }
        }

        captures.push({
          id: id,
          type: 'fetch',
          url: new URL(url, location.href).href,
          method: method.toUpperCase(),
          requestHeaders: reqHeaders,
          requestBody: reqBody,
          status: response.status,
          statusText: response.statusText,
          responseHeaders: resHeaders,
          responseBody: isJson ? parsedBody : (body.length < 2000 ? body : '[truncated ' + body.length + ' chars]'),
          responseSize: body.length,
          contentType: contentType,
          isJson: isJson,
          latency: Date.now() - startTime,
          timestamp: new Date().toISOString(),
        });
      }).catch(function() {});

      return response;
    });
  };

  // ---- XHR interceptor ----
  var origOpen = XMLHttpRequest.prototype.open;
  var origSend = XMLHttpRequest.prototype.send;
  var origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function(method, url) {
    this.__apiRip = {
      method: method,
      url: url,
      headers: {},
      body: null,
      startTime: 0,
    };
    return origOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    if (this.__apiRip) {
      this.__apiRip.headers[name] = value;
    }
    return origSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    if (this.__apiRip) {
      this.__apiRip.body = body;
      this.__apiRip.startTime = Date.now();

      var self = this;
      var id = ++captureId;

      this.addEventListener('load', function() {
        var contentType = self.getResponseHeader('content-type') || '';
        var isJson = contentType.indexOf('json') !== -1;
        var resBody = self.responseText || '';
        var parsedBody = null;
        if (isJson) {
          try { parsedBody = JSON.parse(resBody); } catch(e) {}
        }

        var resHeaders = {};
        var rawHeaders = self.getAllResponseHeaders().trim().split(/[\r\n]+/);
        rawHeaders.forEach(function(line) {
          var parts = line.split(': ');
          if (parts.length >= 2) {
            resHeaders[parts[0].toLowerCase()] = parts.slice(1).join(': ');
          }
        });

        captures.push({
          id: id,
          type: 'xhr',
          url: new URL(self.__apiRip.url, location.href).href,
          method: self.__apiRip.method.toUpperCase(),
          requestHeaders: self.__apiRip.headers,
          requestBody: self.__apiRip.body,
          status: self.status,
          statusText: self.statusText,
          responseHeaders: resHeaders,
          responseBody: isJson ? parsedBody : (resBody.length < 2000 ? resBody : '[truncated ' + resBody.length + ' chars]'),
          responseSize: resBody.length,
          contentType: contentType,
          isJson: isJson,
          latency: Date.now() - self.__apiRip.startTime,
          timestamp: new Date().toISOString(),
        });
      });
    }
    return origSend.apply(this, arguments);
  };

  // ---- Replay function: scan Performance API & re-fetch missed API requests ----
  var skipExt = /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|map|webp)(\?|$)/i;
  var skipDomains = /(google-analytics|googletagmanager|facebook\.net|doubleclick|fonts\.googleapis|fonts\.gstatic|cloudflareinsights)/i;

  function replay() {
    var entries = performance.getEntriesByType('resource');
    var toReplay = [];

    for (var i = 0; i < entries.length; i++) {
      var u = entries[i].name;
      if (replayedUrls[u]) continue;
      if (skipExt.test(u)) continue;
      if (skipDomains.test(u)) continue;

      var isSameOrigin = u.indexOf(location.origin) === 0;
      var isApiLike = /\/api\/|\/v[0-9]+\/|\/graphql|\/rest\/|\/rpc\/|\.json|\/data\/|\/query/i.test(u);
      if (isSameOrigin || isApiLike) {
        toReplay.push(u);
        replayedUrls[u] = true;
      }
    }

    var promises = [];
    for (var k = 0; k < toReplay.length; k++) {
      promises.push((function(fetchUrl) {
        return origFetch.call(window, fetchUrl, {method: 'GET', credentials: 'same-origin'})
          .then(function(response) {
            var cloned = response.clone();
            var resHeaders = {};
            cloned.headers.forEach(function(v, hk) { resHeaders[hk] = v; });

            return cloned.text().then(function(body) {
              var contentType = resHeaders['content-type'] || '';
              var isJson = contentType.indexOf('json') !== -1;
              var parsedBody = null;
              if (isJson) {
                try { parsedBody = JSON.parse(body); } catch(e) { parsedBody = null; }
              }

              captures.push({
                id: ++captureId,
                type: 'replay',
                url: fetchUrl,
                method: 'GET',
                requestHeaders: {},
                requestBody: null,
                status: response.status,
                statusText: response.statusText,
                responseHeaders: resHeaders,
                responseBody: isJson ? parsedBody : (body.length < 2000 ? body : '[truncated ' + body.length + ' chars]'),
                responseSize: body.length,
                contentType: contentType,
                isJson: isJson,
                latency: 0,
                timestamp: new Date().toISOString(),
              });
            });
          }).catch(function() {});
      })(toReplay[k]));
    }

    return Promise.all(promises).then(function() {
      return toReplay.length;
    });
  }

  window.__API_RIP__ = {
    getCaptures: function() { return captures; },
    getCaptureCount: function() { return captures.length; },
    clear: function() { captures = []; captureId = 0; replayedUrls = {}; },
    replay: replay,
  };

  return JSON.stringify({status: 'interceptor_installed', captured: 0});
})()
