import React, { useState, useEffect } from 'react';
import {
  fetchGalleryImages,
  uploadGalleryImage,
  deleteGalleryImage,
  setFeaturedImage,
} from '../../services/galleryApi';
import FileUpload from '../common/FileUpload';
import './Gallery.css';

const Gallery = () => {
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadImages();
  }, []);

  const loadImages = async () => {
    try {
      const data = await fetchGalleryImages();
      setImages(data);
    } catch (err) {
      console.error('Failed to load gallery:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleUpload = async (file, onProgress) => {
    try {
      await uploadGalleryImage(file, onProgress);
      await loadImages();
    } catch (err) {
      alert('이미지 업로드에 실패했습니다.');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('이 이미지를 삭제하시겠습니까?')) return;
    try {
      await deleteGalleryImage(id);
      setImages(prev => prev.filter(img => img.id !== id));
    } catch (err) {
      alert('이미지 삭제에 실패했습니다.');
    }
  };

  const handleSetFeatured = async (id) => {
    try {
      await setFeaturedImage(id);
      setImages(prev => prev.map(img => ({
        ...img,
        is_featured: img.id === id,
      })));
    } catch (err) {
      alert('대표 이미지 설정에 실패했습니다.');
    }
  };

  if (loading) {
    return <div className="gallery"><div className="loading">로딩 중...</div></div>;
  }

  return (
    <div className="gallery">
      <div className="gallery-header">
        <h2>갤러리</h2>
      </div>

      <FileUpload
        onUpload={handleUpload}
        accept=".png,.jpg,.jpeg,.gif,.webp"
      />

      {images.length > 0 ? (
        <div className="gallery-grid">
          {images.map((img) => (
            <div key={img.id} className={`gallery-card ${img.is_featured ? 'featured' : ''}`}>
              <div className="gallery-image-wrapper">
                <img src={img.url} alt={img.filename} className="gallery-image" />
                {img.is_featured && <span className="featured-badge">대표</span>}
              </div>
              <div className="gallery-card-info">
                <span className="gallery-filename">{img.filename}</span>
                <div className="gallery-card-actions">
                  {!img.is_featured && (
                    <button className="gallery-featured-btn" onClick={() => handleSetFeatured(img.id)}>
                      대표로 설정
                    </button>
                  )}
                  <button className="gallery-delete-btn" onClick={() => handleDelete(img.id)}>
                    삭제
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-state-box">
          <p>등록된 이미지가 없습니다.</p>
        </div>
      )}
    </div>
  );
};

export default Gallery;
