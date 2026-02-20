import React, { useEffect, useRef, useState, useCallback } from 'react';
import './LocationPicker.css';

const NAVER_MAP_CLIENT_ID = import.meta.env.VITE_NAVER_MAP_CLIENT_ID;
const DEFAULT_CENTER = { lat: 37.5665, lng: 126.978 }; // 서울 시청

function loadNaverMapScript() {
  return new Promise((resolve, reject) => {
    if (window.naver && window.naver.maps) {
      resolve();
      return;
    }
    const existing = document.getElementById('naver-map-script');
    if (existing) {
      existing.addEventListener('load', resolve);
      existing.addEventListener('error', reject);
      return;
    }
    const script = document.createElement('script');
    script.id = 'naver-map-script';
    script.src = `https://oapi.map.naver.com/openapi/v3/maps.js?ncpClientId=${NAVER_MAP_CLIENT_ID}&submodules=geocoder`;
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error('Naver Map SDK 로딩 실패'));
    document.head.appendChild(script);
  });
}

const LocationPicker = ({ location, latitude, longitude, onChange, onClose }) => {
  const mapRef = useRef(null);
  const mapInstance = useRef(null);
  const markerRef = useRef(null);
  const [searchQuery, setSearchQuery] = useState(location || '');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const updateMarker = useCallback((lat, lng, address) => {
    const { naver } = window;
    const pos = new naver.maps.LatLng(lat, lng);

    if (markerRef.current) {
      markerRef.current.setPosition(pos);
    } else {
      markerRef.current = new naver.maps.Marker({
        position: pos,
        map: mapInstance.current,
      });
    }
    mapInstance.current.setCenter(pos);

    if (address) {
      setSearchQuery(address);
    }
  }, []);

  const reverseGeocode = useCallback((lat, lng) => {
    const { naver } = window;
    naver.maps.Service.reverseGeocode(
      { coords: new naver.maps.LatLng(lat, lng), orders: 'roadaddr,addr' },
      (status, response) => {
        if (status !== naver.maps.Service.Status.OK) return;
        const result = response.v2.address;
        const address = result.roadAddress || result.jibunAddress || '';
        setSearchQuery(address);
        onChange({ location: address, latitude: lat, longitude: lng });
      }
    );
  }, [onChange]);

  useEffect(() => {
    if (!NAVER_MAP_CLIENT_ID || NAVER_MAP_CLIENT_ID === 'YOUR_CLIENT_ID_HERE') {
      setError('Naver Map Client ID가 설정되지 않았습니다.');
      setLoading(false);
      return;
    }

    loadNaverMapScript()
      .then(() => {
        const { naver } = window;
        const center = latitude && longitude
          ? new naver.maps.LatLng(latitude, longitude)
          : new naver.maps.LatLng(DEFAULT_CENTER.lat, DEFAULT_CENTER.lng);

        mapInstance.current = new naver.maps.Map(mapRef.current, {
          center,
          zoom: 15,
          zoomControl: true,
          zoomControlOptions: { position: naver.maps.Position.TOP_RIGHT },
        });

        if (latitude && longitude) {
          updateMarker(latitude, longitude, null);
        }

        naver.maps.Event.addListener(mapInstance.current, 'click', (e) => {
          const lat = e.coord.lat();
          const lng = e.coord.lng();
          updateMarker(lat, lng, null);
          reverseGeocode(lat, lng);
        });

        setLoading(false);
      })
      .catch(() => {
        setError('지도를 불러오는데 실패했습니다.');
        setLoading(false);
      });
  }, [latitude, longitude, updateMarker, reverseGeocode]);

  const handleSearch = (e) => {
    e.preventDefault();
    if (!searchQuery.trim() || !window.naver?.maps?.Service) return;

    const { naver } = window;
    naver.maps.Service.geocode({ query: searchQuery.trim() }, (status, response) => {
      if (status !== naver.maps.Service.Status.OK) {
        setError('검색 결과가 없습니다.');
        setTimeout(() => setError(''), 2000);
        return;
      }
      const item = response.v2.addresses[0];
      if (!item) {
        setError('검색 결과가 없습니다.');
        setTimeout(() => setError(''), 2000);
        return;
      }
      const lat = parseFloat(item.y);
      const lng = parseFloat(item.x);
      const address = item.roadAddress || item.jibunAddress || searchQuery.trim();
      updateMarker(lat, lng, address);
      onChange({ location: address, latitude: lat, longitude: lng });
    });
  };

  const handleConfirm = () => {
    onClose();
  };

  return (
    <div className="location-picker">
      <form className="location-search" onSubmit={handleSearch}>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="주소 또는 장소명 검색"
        />
        <button type="submit">검색</button>
      </form>

      {error && <p className="location-error">{error}</p>}

      <div className="location-map" ref={mapRef}>
        {loading && <div className="location-loading">지도 로딩 중...</div>}
      </div>

      <div className="location-actions">
        <p className="location-hint">지도를 클릭하여 위치를 선택하세요</p>
        <button type="button" className="location-confirm-btn" onClick={handleConfirm}>
          확인
        </button>
      </div>
    </div>
  );
};

export default LocationPicker;
