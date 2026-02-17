import React, { useState, useEffect, useCallback } from 'react';
import Calendar from 'react-calendar';
import { fetchRehearsals } from '../../services/rehearsalApi';
import { fetchSongs } from '../../services/api';
import RehearsalDetail from './RehearsalDetail';
import RehearsalModal from './RehearsalModal';
import 'react-calendar/dist/Calendar.css';
import './RehearsalCalendar.css';

const RehearsalCalendar = () => {
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [activeMonth, setActiveMonth] = useState(new Date());
  const [rehearsals, setRehearsals] = useState([]);
  const [songs, setSongs] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRehearsal, setEditingRehearsal] = useState(null);

  const loadRehearsals = useCallback(async (date) => {
    try {
      const year = date.getFullYear();
      const month = date.getMonth() + 1;
      const data = await fetchRehearsals(year, month);
      setRehearsals(data);
    } catch (error) {
      console.error('Failed to load rehearsals:', error);
    }
  }, []);

  const loadSongs = useCallback(async () => {
    try {
      const data = await fetchSongs();
      setSongs(data);
    } catch (error) {
      console.error('Failed to load songs:', error);
    }
  }, []);

  useEffect(() => {
    loadRehearsals(activeMonth);
    loadSongs();
  }, [activeMonth, loadRehearsals, loadSongs]);

  const handleActiveStartDateChange = ({ activeStartDate }) => {
    setActiveMonth(activeStartDate);
  };

  const handleDateClick = (date) => {
    setSelectedDate(date);
  };

  const handleAddClick = () => {
    setEditingRehearsal(null);
    setModalOpen(true);
  };

  const handleEdit = (rehearsal) => {
    setEditingRehearsal(rehearsal);
    setModalOpen(true);
  };

  const handleModalClose = () => {
    setModalOpen(false);
    setEditingRehearsal(null);
  };

  const handleModalSave = () => {
    handleModalClose();
    loadRehearsals(activeMonth);
  };

  const handleDelete = () => {
    loadRehearsals(activeMonth);
  };

  // 특정 날짜에 해당하는 rehearsal 목록
  const getRehearsalsForDate = (date) => {
    const dateStr = date.toLocaleDateString('en-CA'); // YYYY-MM-DD
    return rehearsals.filter((r) => {
      if (r.date === dateStr) return true;
      if (r.start_date && r.end_date) {
        return dateStr >= r.start_date && dateStr <= r.end_date;
      }
      return false;
    });
  };

  // 달력 타일에 도트 표시
  const tileContent = ({ date, view }) => {
    if (view !== 'month') return null;
    const items = getRehearsalsForDate(date);
    if (items.length === 0) return null;

    return (
      <div className="rehearsal-dots">
        {items.slice(0, 3).map((r) => (
          <span
            key={r.id}
            className="rehearsal-dot"
            style={{ backgroundColor: r.color || '#ffd32a' }}
          />
        ))}
      </div>
    );
  };

  // 기간 일정 하이라이트
  const tileClassName = ({ date, view }) => {
    if (view !== 'month') return null;
    const dateStr = date.toLocaleDateString('en-CA');
    const classes = [];

    for (const r of rehearsals) {
      if (r.start_date && r.end_date && dateStr >= r.start_date && dateStr <= r.end_date) {
        classes.push('period-highlight');
        if (dateStr === r.start_date) classes.push('period-start');
        if (dateStr === r.end_date) classes.push('period-end');
      }
    }

    return classes.length > 0 ? classes.join(' ') : null;
  };

  const selectedRehearsals = getRehearsalsForDate(selectedDate);

  return (
    <div className="rehearsal-calendar-wrapper">
      <div className="calendar-header">
        <h3>📅 합주 일정</h3>
        <button className="calendar-add-btn" onClick={handleAddClick}>
          + 일정 추가
        </button>
      </div>

      <div className="calendar-body">
        <div className="calendar-left">
          <Calendar
            onChange={handleDateClick}
            value={selectedDate}
            onActiveStartDateChange={handleActiveStartDateChange}
            tileContent={tileContent}
            tileClassName={tileClassName}
            locale="ko-KR"
            calendarType="gregory"
            formatDay={(locale, date) => date.getDate()}
          />
        </div>

        <div className="calendar-right">
          <RehearsalDetail
            date={selectedDate}
            rehearsals={selectedRehearsals}
            onEdit={handleEdit}
            onDelete={handleDelete}
            onAdd={handleAddClick}
          />
        </div>
      </div>

      {modalOpen && (
        <RehearsalModal
          rehearsal={editingRehearsal}
          songs={songs}
          defaultDate={selectedDate}
          onClose={handleModalClose}
          onSave={handleModalSave}
        />
      )}
    </div>
  );
};

export default RehearsalCalendar;
