const $ = id => document.getElementById(id);
const escape = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ago = value => value ? new Date(value * 1000).toLocaleTimeString() : 'sin datos';
function render(data) {
  const aps = new Map(data.aps.map(a => [a.id,a]));
  $('connection').textContent = data.acs_error ? '● Sin conexión al ACS' : '● GenieACS conectado';
  $('connection').className = data.acs_error ? 'offline' : '';
  $('summary').innerHTML = [[data.aps.length,'Puntos de acceso'],[data.aps.reduce((n,a)=>n+new Set(a.radios.filter(r=>!r.stale && r.enabled !== false).map(r=>r.band)).size,0),'Radios activas'],[data.clients.filter(c=>!c.stale).length,'Asociaciones observadas'],[data.unifi_enabled ? 'Activo' : 'Local','Envío a UniFi']].map(([n,label])=>`<div class="metric"><strong>${escape(n)}</strong><span>${label}</span></div>`).join('');
  $('warnings').textContent = data.warnings.join(' ');
  $('updated').textContent = 'Última consulta ACS: ' + ago(data.acs_last_collection);
  $('aps').innerHTML = data.aps.map(ap=>{
    const u = data.unifi[ap.virtual_mac];
    const c = u?.configuration;
    const phases = {baseline:'Base inicial registrada (sin cambios)',confirmed:'Cambios solicitados confirmados por lectura',queued:'Escritura pendiente en ACS',reading:'Esperando lectura de confirmación',submitting:'Enviando al ACS',uncertain:'Resultado incierto; no se reintenta automáticamente',failed:'Falló la aplicación',rejected:'Configuración rechazada'};
    return `<article class="ap"><span class="badge ${ap.online?'':'offline'}">${ap.online?'Inform reciente':'sin Inform reciente'}</span><h3>${escape(ap.name)}</h3><p>${escape(ap.ip)} · ${escape(ap.firmware)}</p><p>Asociaciones: ${ap.client_count} · Inform: ${ago(ap.last_inform)}</p><p>${ap.radios.map(r=>`${escape(r.ssid)} · ${escape(r.band)} · canal ${r.channel}${r.enabled===false?' · desactivada':''}${r.stale?' · antiguo':''}`).join('<br>') || 'Sin telemetría de radios'}</p><p class="muted">UniFi virtual: ${escape(ap.virtual_mac)}<br>${u?escape(u.phase)+(u.http_status?' · HTTP '+escape(u.http_status):''):'Emisor '+(data.unifi_enabled?'pendiente':'desactivado')}</p><p>Control: ${c?.enabled?escape(phases[c.phase] || 'Esperando base inicial'):'desactivado'}${c?.error?'<br>'+escape(c.error):''}</p></article>`;
  }).join('') || '<p>Esperando al primer Inform de Huawei…</p>';
  $('clients').innerHTML = data.clients.map(c=>`<tr><td>${escape(c.hostname || 'Sin nombre')}<small>${escape(c.mac)}</small></td><td>${escape(c.ip)}</td><td>${escape(aps.get(c.ap_id)?.name)}</td><td>${escape(c.band)}</td><td>${c.signal == null?'—':escape(c.signal)+' dBm'}</td><td>${ago(c.updated_at)}${c.stale?' · antiguo':''}</td></tr>`).join('');
  const mesh = data.mesh || {nodes: [], clients: []};
  const web = data.web_status;
  $('web-status').textContent = !web?.enabled ? 'Fuente web desactivada' : `Sólo lectura · última consulta: ${ago(web.last_success)}${web.error ? ' · error: '+web.error : ''}`;
  const classifications = {reported: 'Declarado por la fuente; no confirmado', candidate: 'Candidato mesh; identidad pendiente', conflict: 'Identidad/rol en conflicto'};
  $('mesh').innerHTML = mesh.nodes.map(n=>`<article class="ap"><h3>${escape(n.hostname || n.mac)}</h3><p>${escape(classifications[n.classification] || 'Sin verificar')}</p><p>${escape(n.mac)} · ${escape(n.ip)}</p><p>Fabricante por prefijo MAC: ${escape(n.manufacturer)}</p><p>Modelo: ${escape(n.model)} · firmware: ${escape(n.firmware)}</p><p>Rol declarado: ${escape(n.reported_role)} · superior: ${escape(n.parent_mac)}</p><p>Actividad HostInfo (no prueba disponibilidad del nodo): ${n.online === true ? 'activa' : n.online === false ? 'inactiva' : 'desconocida'} · ${n.stale ? 'antiguo' : 'reciente'}</p><p>Fuente: ${escape(n.sources.join(', '))} · ${ago(n.updated_at)}</p><p>Inventario local; no se adopta automáticamente.</p></article>`).join('');
  $('mesh-clients').innerHTML = mesh.clients.map(c=>`<tr><td>${escape(c.hostname || c.mac)}<small>${escape(c.mac)}</small></td><td>${escape(c.parent_mac)}</td><td>${escape(c.band)}</td><td>${c.wifi_generation ? 'Wi-Fi '+escape(c.wifi_generation) : 'Desconocida'}</td><td>${ago(c.updated_at)}${c.stale ? ' · antiguo' : ''}</td></tr>`).join('');
}
async function update() {
  try {const response = await fetch('/api/snapshot'); if (!response.ok) throw Error(response.status); render(await response.json());}
  catch (error) {$('connection').textContent = 'Sin conexión al servicio'; $('connection').className='offline';}
}
update(); setInterval(update, 10000);
