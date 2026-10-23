import { useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { normalizedJoinName, validJoinName } from '../../services/joinName.js';
import MemberPositionPicker from './MemberPositionPicker.jsx';
import { memberPositionLabels } from './memberPositions.js';
import './MemberProfile.css';

export const MemberProfileSummary = ({ member, compact = false }) => {
  const labels = memberPositionLabels(member.positions, member.custom_position);
  return <div className="member-profile-summary">
    <div className="member-position-tags" aria-label="포지션">{labels.length ? labels.map((label, index) => <span key={index}>{label}</span>) : <span>{member.instrument || '포지션 미설정'}</span>}</div>
    <p className={compact ? 'member-biography member-biography-compact' : 'member-biography'}>{member.biography || (member.is_self ? '소개를 작성해 주세요.' : '아직 등록한 소개가 없습니다.')}</p>
  </div>;
};

// The workspace keys this component by band/account. Every read and mutation
// also carries a cancellation signal, including conflict recovery reads.
export const MemberProfileEditor = ({ band, api, expectedMemberId, onSaved, onCancel }) => {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [conflict, setConflict] = useState(false);
  const request = useRef(null);
  const flight = useRef(false);
  const loadedMemberId = useRef(null);
  const original = useRef(null);
  const heading = useRef(null);
  const path = bandWorkspacePaths.myMemberProfile(band.id);
  const nameError = !profile || validJoinName(profile.name) ? '' : normalizedJoinName(profile.name).length === 0
    ? '이름을 입력해 주세요. 공백만으로 저장할 수 없습니다.'
    : Array.from(normalizedJoinName(profile.name)).length > 100
      ? '이름은 100자 이내로 입력해 주세요.'
      : '이름에 표시할 수 없는 문자가 포함되어 있습니다.';
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    api.requestJson(path, { signal: controller.signal }).then(({ member }) => {
      if (controller.signal.aborted) return;
      if (!member?.can_edit_profile || !member.is_self || !Number.isInteger(member.profile_revision) || member.profile_revision < 0 || !Number.isSafeInteger(member.id) || member.id <= 0 || (member.band_id != null && Number(member.band_id) !== Number(band.id)) || (expectedMemberId != null && Number(member.id) !== Number(expectedMemberId))) throw new Error('Unavailable profile');
      loadedMemberId.current = member.id;
      original.current = member;
      setProfile({ name: member.name || '', biography: member.biography || '', positions: member.positions || [], custom_position: member.custom_position || '', profile_revision: member.profile_revision });
      setLoading(false); setError(''); setConflict(false);
      heading.current?.focus();
    }).catch(() => {
      if (!controller.signal.aborted) { setLoading(false); setError('내 프로필을 불러오지 못했습니다. 활성 멤버인지 확인하고 다시 시도해 주세요.'); }
    });
    return () => { controller.abort(); request.current?.abort(); };
  }, [api, path, reload, band.id, expectedMemberId]);
  const retryRead = () => { setProfile(null); setLoading(true); setError(''); setReload((value) => value + 1); };
  const changePositions = (positions, custom_position) => setProfile((previous) => ({ ...previous, positions, custom_position }));
  const save = async (event) => {
    event.preventDefault();
    if (flight.current || conflict || !profile || !validJoinName(profile.name)) return;
    flight.current = true; setSaving(true); setError('');
    const controller = new AbortController(); request.current = controller;
    try {
      // A legacy instrument label must survive an introduction-only edit.
      // Omit unchanged structured fields rather than converting [] to an
      // explicit request to clear the preserved legacy display value.
      const payload = { profile_revision: profile.profile_revision };
      if (normalizedJoinName(profile.name) !== normalizedJoinName(original.current.name || '')) payload.name = normalizedJoinName(profile.name);
      if (profile.biography !== (original.current.biography || '')) payload.biography = profile.biography;
      if (JSON.stringify([...profile.positions].sort()) !== JSON.stringify([...(original.current.positions || [])].sort())) payload.positions = profile.positions;
      if (profile.custom_position !== (original.current.custom_position || '')) payload.custom_position = profile.custom_position;
      if (!Object.hasOwn(payload, 'name') && !Object.hasOwn(payload, 'biography') && !Object.hasOwn(payload, 'positions') && !Object.hasOwn(payload, 'custom_position')) payload.biography = profile.biography;
      const { member } = await api.requestJson(path, { method: 'PATCH', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!member?.is_self || !member.can_edit_profile || member.id !== loadedMemberId.current || !Number.isInteger(member.profile_revision) || member.profile_revision <= profile.profile_revision || (member.band_id != null && Number(member.band_id) !== Number(band.id))) throw new Error('Unexpected saved profile');
      if (!controller.signal.aborted) onSaved(member);
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (failure?.status === 409) { setConflict(true); setError('다른 화면에서 프로필이 변경되었습니다. 최신 정보를 다시 불러온 뒤 수정해 주세요.'); }
        else if ([401, 403, 404].includes(failure?.status)) { setConflict(true); setError('수정 권한을 확인할 수 없습니다. 최신 정보를 다시 불러와 주세요.'); }
        else { setConflict(true); setError('저장 결과를 확인하지 못했습니다. 다시 저장하기 전에 최신 정보를 불러와 주세요.'); }
      }
    } finally { flight.current = false; if (!controller.signal.aborted) setSaving(false); }
  };
  return <form className="workspace-form member-profile-editor" onSubmit={save} aria-labelledby="member-profile-editor-title" aria-busy={loading || saving}>
    <h2 id="member-profile-editor-title" tabIndex={-1} ref={heading}>내 프로필 수정</h2>
    <p className="workspace-muted">이 밴드의 이름·소개·포지션만 변경됩니다. 소개와 포지션은 선택 사항입니다. 멤버 명단이 공개된 밴드에서는 비로그인 방문자에게도 보일 수 있으니 연락처 등 민감한 정보는 적지 마세요.</p>
    {loading && <p role="status">내 프로필을 불러오는 중입니다…</p>}
    {profile && <fieldset disabled={saving || conflict} className="member-profile-fields"><legend>이름·소개·포지션</legend>
      <label htmlFor="member-band-name">밴드에서 사용할 이름{/* 100 Unicode code points can occupy 200 UTF-16 units; validJoinName enforces the actual 100-character limit. */}<input id="member-band-name" required maxLength={200} value={profile.name} onChange={(event) => setProfile((previous) => ({ ...previous, name: event.target.value }))} aria-invalid={Boolean(nameError)} aria-describedby={`member-band-name-help${nameError ? ' member-band-name-error' : ''}`} /></label>
      <small id="member-band-name-help">이 밴드의 멤버 목록과 프로필에 표시됩니다. {Array.from(profile.name).length}/100자</small>
      {nameError && <p id="member-band-name-error" className="workspace-error" role="alert">{nameError}</p>}
      <label htmlFor="member-biography">간단한 소개<textarea id="member-biography" maxLength={300} value={profile.biography} onChange={(event) => setProfile((previous) => ({ ...previous, biography: event.target.value }))} aria-describedby="member-biography-count" /></label>
      <small id="member-biography-count">{Array.from(profile.biography).length}/300자 · 가입 심사 때 작성한 소개와는 별개입니다.</small>
      <MemberPositionPicker positions={profile.positions} customPosition={profile.custom_position} onChange={changePositions} idPrefix="member-profile-position" />
      {!original.current?.positions?.length && original.current?.instrument && <p className="workspace-muted">기존 포지션 표기: {original.current.instrument}. 새 포지션을 선택하면 이 표기가 변경됩니다.</p>}
    </fieldset>}
    {error && <p className="workspace-error" role="alert">{error}</p>}
    {(conflict || (!loading && !profile)) && <button type="button" className="secondary-btn" onClick={retryRead}>최신 프로필 다시 불러오기</button>}
    <div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button type="submit" className="primary-btn" disabled={loading || saving || conflict || !profile || Boolean(nameError)}>{saving ? '저장 중…' : '저장'}</button></div>
  </form>;
};
