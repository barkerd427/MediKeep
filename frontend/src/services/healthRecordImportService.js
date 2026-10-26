/**
 * Health Record Import Service
 * Handles API calls for importing C-CDA, MyChart zip and FHIR Bundle files
 */

import { apiClient } from './apiClient';

class HealthRecordImportService {
  /**
   * Get accepted upload formats and the size limit
   * @returns {Promise<{formats: Array, max_file_size_bytes: number, import_sources: string[]}>}
   */
  async getFormats() {
    const response = await apiClient.get('/health-records/import/formats');
    return response?.data ?? response;
  }

  /**
   * Preview a health record import
   * @param {number} patientId
   * @param {File} file - .xml, .zip or .json
   * @returns {Promise<Object>} Preview response with counts per record type
   */
  async previewImport(patientId, file) {
    const formData = new FormData();
    formData.append('patient_id', String(patientId));
    formData.append('file', file);
    const response = await apiClient.postForm(
      '/health-records/import/preview',
      formData
    );
    return response?.data ?? response;
  }

  /**
   * Execute a health record import
   * @param {number} patientId
   * @param {File} file - re-uploaded
   * @param {boolean} updateExisting - when false, rows imported earlier are left unchanged
   * @returns {Promise<Object>} Import result with counts
   */
  async executeImport(patientId, file, updateExisting = true) {
    const formData = new FormData();
    formData.append('patient_id', String(patientId));
    formData.append('update_existing', String(updateExisting));
    formData.append('file', file);
    const response = await apiClient.postForm(
      '/health-records/import/execute',
      formData
    );
    return response?.data ?? response;
  }
}

export const healthRecordImportService = new HealthRecordImportService();
export default healthRecordImportService;
