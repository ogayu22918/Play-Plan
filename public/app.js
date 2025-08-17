(function(){
  'use strict';
  var geoBtn = document.getElementById('btnLocate');
  var geoStatus = document.getElementById('geoStatus');
  var addrFallback = document.getElementById('addrFallback');
  var form = document.getElementById('planForm');
  var cardsEl = document.getElementById('cards');
  var toastEl = document.getElementById('toast');
  var submitBtn = document.getElementById('btnSubmit');
  var globalLoading = document.getElementById('globalLoading');

  var lastPosition = null;
  var toastTimer = null;

  function show(el){ el.classList.remove('hidden'); }
  function hide(el){ el.classList.add('hidden'); }

  function showToast(msg, isError){
    toastEl.textContent = msg;
    toastEl.className = 'toast' + (isError?' toast--error':'');
    show(toastEl);
    if(toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ hide(toastEl); }, 4000);
  }

  function renderSkeleton(count){
    cardsEl.innerHTML='';
    for(var i=0;i<count;i++){
      var d=document.createElement('div');
      d.className='card skeleton';
      cardsEl.appendChild(d);
    }
  }

  function clearCards(){ cardsEl.innerHTML=''; }

  function displaySuggestions(suggestions, fallbackHint) {
    var cardsEl = document.getElementById('cards');
    cardsEl.innerHTML = '';
    if(fallbackHint) {
      var notice = document.createElement('div');
      notice.className = 'fallback-notice';
      notice.style.cssText = 'background:#fef7cd;border:1px solid #f59e0b;padding:12px;margin-bottom:16px;border-radius:8px;color:#92400e;';
      notice.textContent = fallbackHint;
      cardsEl.appendChild(notice);
    }
    
    var blocks = suggestions.split(/(?=\d+\.)/);
    blocks.forEach(function (b, i) {
      if (!b.trim()) return;
      var card = document.createElement('div');
      card.className = 'card';
      var title = document.createElement('h2');
      title.className = 'card__title';
      var linesB = b.split(/\n/);
      title.textContent = (linesB[0] || ('プラン ' + (i + 1))).substring(0, 60);
      var body = document.createElement('div');
      body.className = 'card__body';
      body.textContent = linesB.slice(1).join('\n').substring(0, 500) || '詳細なし';
      var meta = document.createElement('div');
      meta.className = 'card__meta';
      meta.textContent = '提案#' + (i + 1);
      card.appendChild(title);
      card.appendChild(body);
      card.appendChild(meta);
      cardsEl.appendChild(card);
    });
  }

  function displayCandidatesWithPlaces(candidates) {
    if (!candidates || !candidates.length) return;
    
    var cardsEl = document.getElementById('cards');
    var placesSection = document.createElement('div');
    placesSection.className = 'places-section';
    placesSection.style.cssText = 'margin-top:20px;padding:16px;background:#f8fafc;border-radius:8px;';
    
    var placesTitle = document.createElement('h3');
    placesTitle.textContent = '🏪 近隣の具体的な場所';
    placesTitle.style.cssText = 'margin:0 0 12px 0;color:#374151;';
    placesSection.appendChild(placesTitle);
    
    candidates.forEach(function(candidate) {
      if (!candidate.places || !candidate.places.length) return;
      
      var candidateDiv = document.createElement('div');
      candidateDiv.style.cssText = 'margin-bottom:12px;';
      
      var candidateTitle = document.createElement('div');
      candidateTitle.textContent = '📍 ' + candidate.name;
      candidateTitle.style.cssText = 'font-weight:600;margin-bottom:6px;color:#1f2937;';
      candidateDiv.appendChild(candidateTitle);
      
      candidate.places.forEach(function(place) {
        var placeDiv = document.createElement('div');
        placeDiv.style.cssText = 'margin-left:16px;margin-bottom:4px;display:flex;align-items:center;gap:8px;';
        
        var placeName = document.createElement('span');
        placeName.textContent = place.name;
        placeName.style.cssText = 'color:#374151;';
        
        var placeDistance = document.createElement('span');
        placeDistance.textContent = '(' + place.distance_km + 'km)';
        placeDistance.style.cssText = 'color:#6b7280;font-size:0.9em;';
        
        var placeLink = document.createElement('a');
        placeLink.href = place.osm_url;
        placeLink.target = '_blank';
        placeLink.rel = 'noopener';
        placeLink.textContent = '地図';
        placeLink.style.cssText = 'color:#2563eb;text-decoration:underline;font-size:0.9em;';
        
        placeDiv.appendChild(placeName);
        placeDiv.appendChild(placeDistance);
        placeDiv.appendChild(placeLink);
        candidateDiv.appendChild(placeDiv);
      });
      
      placesSection.appendChild(candidateDiv);
    });
    
    cardsEl.appendChild(placesSection);
  }

  function getNumber(v){ var n=parseFloat(v); return isNaN(n)?undefined:n; }

  function collectPayload(){
    var fd=new FormData(form);
    var payload={
      mood: (fd.get('mood')||'').trim(),
      radius_km: getNumber(fd.get('radius_km')),
      indoor: (function(v){ if(v==='true') return true; if(v==='false') return false; return undefined;})(fd.get('indoor')),
      budget: (fd.get('budget')||'').trim()
    };
    if(lastPosition){ payload.lat= lastPosition.coords.latitude; payload.lon= lastPosition.coords.longitude; }
    else {
      var lat = (fd.get('lat')||'').trim();
      var lon = (fd.get('lon')||'').trim();
      if(lat && lon){ payload.lat=parseFloat(lat); payload.lon=parseFloat(lon); }
    }
    return payload;
  }

  function validatePayload(p){
    if(typeof p.lat!=='number' || typeof p.lon!=='number' || isNaN(p.lat) || isNaN(p.lon)) return '緯度経度が取得できていません';
    if(p.radius_km && p.radius_km<=0) return '移動半径が不正です';
    return null;
  }

  function submitSuggest(){
    var payload=collectPayload();
    var err=validatePayload(payload);
    if(err){ showToast(err,true); return; }
    renderSkeleton(3);
    show(globalLoading); submitBtn.disabled=true; geoBtn.disabled=true;

    fetch('/api/suggest', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify(payload)
    }).then(function(res){
      if(!res.ok) throw new Error('HTTP '+res.status);
      return res.json();
    }).then(function(json){
      var text = json.suggestions || '';
      var fallbackHint = null;
      
      if(json.fallback || json.degraded){ 
        var reason = json.fallback_reason;
        fallbackHint = 'AI生成ができないため、基本的な提案をお送りしました';
        if(reason === 'timeout') fallbackHint = '処理時間の制限により、基本的な提案をお送りしました';
        if(json.weather_error) fallbackHint += ' (天気データ取得エラー)';
      }
      
      displaySuggestions(text, fallbackHint);
      
      // Display candidates with places if available
      if(json.candidates && json.candidates.length > 0) {
        displayCandidatesWithPlaces(json.candidates);
      }
      
      if(json.fallback || json.degraded){ 
        var reason = json.fallback_reason;
        var msg = 'AI生成ができないため、基本的な提案をお送りしました';
        if(reason === 'timeout') msg = '処理時間の制限により、基本的な提案をお送りしました';
        if(json.weather_error) msg += ' (天気データ取得エラー: ' + json.weather_error + ')';
        showToast(msg, false); 
      }
    }).catch(function(e){
      clearCards();
      showToast('取得失敗: '+ e.message, true);
    }).finally(function(){
      hide(globalLoading); submitBtn.disabled=false; geoBtn.disabled=false;
    });
  }

  geoBtn.addEventListener('click', function(){
    if(!navigator.geolocation){ showToast('Geolocation非対応', true); addrFallback.classList.add('addr-fallback--show'); return; }
    hide(addrFallback); show(geoStatus); geoBtn.disabled=true;
    var done=false;
    navigator.geolocation.getCurrentPosition(function(pos){
      done=true; lastPosition=pos; hide(geoStatus); geoBtn.disabled=false; showToast('位置取得成功', false);
    }, function(err){
      hide(geoStatus); geoBtn.disabled=false; if(!done){ addrFallback.classList.add('addr-fallback--show'); showToast('位置取得失敗: '+ err.message, true); }
    }, { enableHighAccuracy:false, timeout:6000, maximumAge:120000 });
  });

  form.addEventListener('submit', function(ev){ ev.preventDefault(); submitSuggest(); });

})();
