/** 两个物联网插件共用的设备管理视图，复用公共详情和 TDesign 控件。 */
window.HuiYanHardwareManagementTemplate = `

    <t-card class="hardware-management-shell">
        <t-tabs v-model="pageTab" :destroy-on-hide="false" class="hardware-page-tabs"
            @change="onPageTabChange">
            <t-tab-panel value="devices" label="设备管理">
              <div class="hardware-device-panel">
                <div class="hardware-toolbar">
                    <t-space class="hardware-toolbar-actions">
                        <t-button v-if="isHuiyan" v-permission="'hardware:sync'" theme="primary" @click="openRegister">注册设备</t-button>
                        <t-button v-permission="'hardware:sync'" theme="default" :loading="syncing" @click="syncDevices">
                            <template #icon><t-icon name="refresh"></t-icon></template>同步设备
                        </t-button>
                    </t-space>
                    <div class="hardware-filters hardware-management-filters">
                        <t-input v-model="filters.keyword" clearable placeholder="设备名称或编号"
                            class="hardware-search" @enter="search"></t-input>
                        
                    <t-select v-model="filters.deviceType" clearable placeholder="设备类型"
                            class="hardware-filter" :options="deviceTypeOptions" @change="search"></t-select>
                        <t-select v-model="filters.bindingStatus" clearable placeholder="绑定状态"
                            class="hardware-filter" :options="bindingOptions" @change="search"></t-select>
                        <t-select v-model="filters.available" clearable placeholder="平台状态"
                            class="hardware-filter" :options="availableOptions" @change="search"></t-select>
                        <t-button theme="primary" @click="search">查询</t-button>
                    </div>
                    <t-radio-group v-model="viewMode" variant="default-filled" class="hardware-view-switch" aria-label="设备显示方式">
                        <t-radio-button value="cards">卡片</t-radio-button>
                        <t-radio-button value="list">列表</t-radio-button>
                    </t-radio-group>
                </div>

                <t-alert v-if="isHuiyan && registeredDeviceId" theme="success" message="注册成功，请保存设备 ID，用于后续上报和查询。">
                    <template #operation><t-space>
                        <t-input :value="registeredDeviceId" readonly aria-label="新注册设备 ID"></t-input>
                        <t-button variant="text" @click="copyDeviceId(registeredDeviceId)">复制设备 ID</t-button>
                    </t-space></template>
                </t-alert>
                <t-alert v-if="registrationPendingSync" theme="warning" message="设备已注册，同步未完成，请重试">
                    <template #operation><t-button v-permission="'hardware:sync'" :loading="syncing" @click="retryRegistrationSync">重试同步</t-button></template>
                </t-alert>
                <t-alert v-if="syncError" theme="warning" :message="syncError"></t-alert>
                <div class="hardware-view-content" :class="{ 'is-list': viewMode === 'list' }">
                <t-skeleton v-if="loading" :row-col="[1, 1, 1]"></t-skeleton>
                <template v-else-if="viewMode === 'cards'">
                    <div v-if="deviceList.length" class="hardware-card-grid">
                        <t-card v-for="row in deviceList" :key="row.id" class="hardware-device-card" :bordered="false">
                            <div class="hardware-card-heading">
                                <img v-if="row.image_url && failedCardImages[row.id] !== row.image_url" :src="row.image_url"
                                    alt="设备图片" class="hardware-device-thumb" @error="cardImageFailed(row)">
                                <div v-else class="hardware-device-fallback"><t-icon :name="deviceIcon(row.device_type)"></t-icon></div>
                                <div class="hardware-card-identity">
                                    <t-tooltip :content="cardName(row)"><div class="hardware-device-name">[[ cardName(row) ]]</div></t-tooltip>
                                    <div class="hardware-card-code">[[ cardCode(row) ]]</div>
                                </div>
                                <t-tag class="hardware-card-status" :theme="row.available && row.provider_available ? 'success' : 'danger'" variant="light">
                                    [[ !row.provider_available ? '来源停用' : (row.available ? '可用' : '不可用') ]]
                                </t-tag>
                            </div>
                            <div class="hardware-card-facts">
                                <div><span>设备类型</span><strong>[[ row.device_type_label || row.device_type || '未指定' ]]</strong></div>
                                <div><span>绑定位置</span><strong>[[ row.plot_id ? [row.area_name, row.plot_name].filter(Boolean).join(' / ') : '未绑定' ]]</strong></div>
                            </div>
                            <div class="hardware-card-footer">
                                <div><span>最近同步</span><time>[[ formatChinaTime(row.last_sync_time) ]]</time></div>
                                <t-space><t-button theme="primary" variant="text" @click="openDetail(row)">管理</t-button><t-tooltip v-if="isHuiyan" :content="isDeviceBound(row) ? '请先在基本设置解除绑定' : '删除注册设备'">
                                    <span v-permission="'hardware:sync'"><t-button theme="danger" variant="text"
                                        :disabled="isDeviceBound(row) || deleting" @click="openDelete(row)">删除</t-button></span>
                                </t-tooltip></t-space>
                            </div>
                        </t-card>
                    </div>
                    <t-space v-else class="hardware-cards-empty" direction="vertical" align="center">
                        <t-icon name="inbox" size="32px"></t-icon><span>暂无设备</span>
                    </t-space>
                </template>
                <t-table v-else class="hardware-device-table" :data="deviceList" :columns="managementColumns"
                    :pagination="null" row-key="id" hover>
                    <template #device="{ row }">
                        <div class="hardware-device-cell">
                            <img v-if="row.image_url" :src="row.image_url" alt="设备图片" class="hardware-device-thumb">
                            <div v-else class="hardware-device-fallback"><t-icon :name="deviceIcon(row.device_type)"></t-icon></div>
                            <div class="hardware-device-meta">
                                <div class="hardware-device-name">[[ row.nickname || (isHuiyan ? row.device_name : row.device_type_label) || '未命名设备' ]]</div>
                                <div class="hardware-device-code">[[ isHuiyan ? row.provider_device_id : row.device_name ]]</div>
                            </div>
                        </div>
                    </template>
                    <template #type="{ row }"><t-tag variant="light">[[ row.device_type_label || row.device_type || '未指定' ]]</t-tag></template>
                    <template #available="{ row }">
                        <t-tag :theme="row.available && row.provider_available ? 'success' : 'danger'" variant="light">
                            [[ !row.provider_available ? '来源停用' : (row.available ? '可用' : '不可用') ]]
                        </t-tag>
                    </template>
                    <template #binding="{ row }">
                        <div v-if="row.plot_id" class="hardware-binding-text">[[ row.area_name ]]<span>[[ row.plot_name ]]</span></div>
                        <t-tag v-else theme="default" variant="light">未绑定</t-tag>
                    </template>
                    <template #last_sync_time="{ row }">
                        <span class="hardware-time">[[ formatChinaTime(row.last_sync_time) ]]</span>
                    </template>
                    <template #operation="{ row }">
                        <t-space><t-button theme="primary" variant="text" @click="openDetail(row)">管理</t-button><t-tooltip v-if="isHuiyan" :content="isDeviceBound(row) ? '请先在基本设置解除绑定' : '删除注册设备'">
                                    <span v-permission="'hardware:sync'"><t-button theme="danger" variant="text"
                                        :disabled="isDeviceBound(row) || deleting" @click="openDelete(row)">删除</t-button></span>
                                </t-tooltip></t-space>
                    </template>
                </t-table>
                </div>
                <t-pagination class="hardware-view-pagination" :current="pagination.current" :page-size="pagination.pageSize"
                    :total="pagination.total" :show-jumper="false" @change="changeViewPage"></t-pagination>
              </div>
            </t-tab-panel>

            <t-tab-panel v-if="canSchedule" value="schedule" label="调度设置">
                <t-skeleton v-if="settingsLoading" :row-col="[1, 1, 1]"></t-skeleton>
                <t-alert v-if="settingsError" theme="warning" :message="settingsError">
                    <template #operation><t-button @click="fetchAutoFetchSettings">重试</t-button></template>
                </t-alert>
                <t-form v-if="settingsLoaded && !settingsLoading" class="hardware-settings-form" label-width="140px">
                    <t-form-item label="自动采集">
                        <t-switch v-model="autoFetchSettings.enabled"></t-switch>
                    </t-form-item>
                    <t-form-item label="采集周期">
                        <div class="hardware-interval-field">
                            <t-input-number v-model="autoFetchSettings.intervalSeconds"
                                :min="1" :max="86400" :decimal-places="0"></t-input-number>
                            <span>秒</span>
                        </div>
                    </t-form-item>
                    <t-form-item>
                        <t-button v-permission="'hardware:sync'" theme="primary" :loading="settingsSaving"
                            :disabled="settingsLoading" @click="saveAutoFetchSettings">保存设置</t-button>
                    </t-form-item>
                </t-form>
            </t-tab-panel>

            <t-tab-panel v-if="!isHuiyan && canConfigure" value="config" label="平台配置">
                <t-skeleton v-if="configLoading" :row-col="[1, 1, 1]"></t-skeleton>
                <t-alert v-if="configError" theme="warning" :message="configError">
                    <template #operation><t-button @click="loadConfig">重试</t-button></template>
                </t-alert>
                <t-form v-if="configLoaded && !configLoading" class="hardware-settings-form" label-width="140px">
                    <t-form-item label="平台地址">
                        <t-input v-model="configForm.base_url" placeholder="https://farmbot-jjr.jjr.vip"></t-input>
                    </t-form-item>
                    <t-form-item label="访问凭据">
                        <t-input v-model="configForm.credential" type="password" autocomplete="new-password"
                            :placeholder="configForm.credential_configured ? '已配置，留空保持原值' : '请输入平台访问凭据'"></t-input>
                    </t-form-item>
                    <t-form-item><t-button theme="primary" :loading="configSaving" @click="saveConfig">保存配置</t-button></t-form-item>
                </t-form>
            </t-tab-panel>
        </t-tabs>
    </t-card>

    <t-drawer v-model:visible="drawerVisible" class="hardware-drawer" size="820px"
        :header="drawerTitle" :footer="false" :close-btn="true" @close="closeDrawer">
        <t-descriptions v-if="currentDevice" class="hardware-detail-summary" :column="2" bordered>
            <t-descriptions-item :span="2" :label="isHuiyan ? '设备 ID' : '设备编号'">[[ isHuiyan ? currentDevice.provider_device_id : currentDevice.device_name ]]</t-descriptions-item>
            <t-descriptions-item label="设备类型">[[ currentDevice.device_type_label || currentDevice.device_type || '未指定' ]]</t-descriptions-item>
            <t-descriptions-item label="平台状态">[[ currentDevice.available && currentDevice.provider_available ? '可用' : '不可用' ]]</t-descriptions-item>
        </t-descriptions>
        <t-tabs class="hardware-detail-tabs" v-model="detailTab" :destroy-on-hide="false">
            <t-tab-panel value="current" label="设备数据">
                <t-loading :loading="detailLoading">
                    <t-alert v-if="detailError" theme="warning" :message="detailError"></t-alert>
                    <t-button v-if="detailError" variant="text" @click="retryDetail">重新加载详情</t-button>
                    <component v-if="drawerVisible && detailComponent" :is="detailComponent" :key="detailKey"
                        :device="detailDevice" :context="detailContext"></component>
                </t-loading>
            </t-tab-panel>
            
            <t-tab-panel v-if="isHuiyan" value="registration" label="注册信息">
                <t-loading :loading="registrationDetailLoading">
                    <t-alert v-if="registrationDetailError" theme="warning" :message="registrationDetailError">
                        <template #operation><t-button variant="text" @click="loadRegistrationDetail">重试</t-button></template>
                    </t-alert>
                    <template v-if="registrationDetail">
                        <t-descriptions class="hardware-registration-info" :column="2" bordered>
                            <t-descriptions-item label="设备 ID" :span="2"><div class="hardware-registration-id">
                                <span>[[ registrationDetail.device_id ]]</span><t-button variant="text" @click="copyDeviceId(registrationDetail.device_id)">复制</t-button>
                            </div></t-descriptions-item>
                            <t-descriptions-item label="名称">[[ registrationDetail.device_name ]]</t-descriptions-item>
                            <t-descriptions-item label="经纬度">[[ registrationDetail.longitude === '' || registrationDetail.longitude == null || registrationDetail.latitude === '' || registrationDetail.latitude == null ? '未填写' : registrationDetail.longitude + ' / ' + registrationDetail.latitude ]]</t-descriptions-item>
                            <t-descriptions-item label="安装地址" :span="2">[[ registrationDetail.address || '未填写' ]]</t-descriptions-item>
                            <t-descriptions-item label="注册时间">[[ formatChinaTime(registrationDetail.create_time) ]]</t-descriptions-item>
                            <t-descriptions-item label="最近上报">[[ registrationDetail.last_seen ? formatChinaTime(registrationDetail.last_seen) : '尚未上报' ]]</t-descriptions-item>
                        </t-descriptions>
                        <t-card title="扩展信息" class="hardware-registration-json">
                            <template #actions><t-button variant="text" :disabled="!registrationJson" @click="copyRegistrationJson">复制 JSON</t-button></template>
                            <pre v-if="registrationJson" class="hardware-json-content">[[ registrationJson ]]</pre>
                            <t-empty v-else description="暂无扩展信息"></t-empty>
                        </t-card>
                    </template>
                </t-loading>
            </t-tab-panel>
            <t-tab-panel value="basic" label="基本设置">
                <t-form class="hardware-form" label-width="100px">
                    <t-form-item label="设备图片">
                        <image-upload v-model="appearance.imageUrl" hint="用于地图水滴标记"></image-upload>
                    </t-form-item>
                    <t-form-item><t-button v-permission="'hardware:update'" theme="primary" @click="saveAppearance">保存图片</t-button></t-form-item>
                    <t-form-item label="产区">
                        <t-select v-model="binding.areaId" :options="areaOptions" placeholder="选择产区" @change="onAreaChange"></t-select>
                    </t-form-item>
                    <t-form-item label="地块">
                        <t-select v-model="binding.plotId" :options="plotOptions" placeholder="选择地块" :disabled="!binding.areaId"></t-select>
                    </t-form-item>
                    <t-form-item>
                        <div class="hardware-form-actions">
                            <t-button v-permission="'hardware:bind'" theme="primary" :disabled="!binding.plotId" @click="saveBinding">保存绑定</t-button>
                            <t-button v-permission="'hardware:bind'" v-if="currentDevice && currentDevice.plot_id" theme="danger" variant="outline"
                                @click="removeBinding">解除绑定</t-button>
                        </div>
                    </t-form-item>
                    <t-form-item v-if="isHuiyan && currentDevice" label="删除设备">
                        <t-space direction="vertical">
                            <span>删除注册信息和实时数据，不可撤销；已绑定设备请先解除绑定。</span>
                            <t-button v-permission="'hardware:sync'" theme="danger" variant="outline"
                                :disabled="isDeviceBound(currentDevice) || deleting" @click="openDelete(currentDevice)">删除设备</t-button>
                        </t-space>
                    </t-form-item>
                </t-form>
            </t-tab-panel>
        </t-tabs>
    </t-drawer>

    <t-dialog v-model:visible="quickDetectDialogVisible" header="快捷检测结果"
        width="1040px" :footer="false" :close-on-overlay-click="false"
        @close="closeQuickDetect">
        <t-alert :theme="quickDetectStatusTheme" :message="quickDetectStatusMessage"
            class="hardware-detect-status"></t-alert>
        <t-loading v-if="quickDetectWorking && !quickDetectRecord" loading
            text="模型正在处理当前图片" class="hardware-detect-loading"></t-loading>
        <template v-if="quickDetectRecord">
            <t-descriptions :column="4" bordered class="hardware-detect-summary">
                <t-descriptions-item label="地块">[[ quickDetectRecord.area_name ]] / [[ quickDetectRecord.plot_name ]]</t-descriptions-item>
                <t-descriptions-item label="模型">[[ quickDetectRecord.model_name ]] [[ quickDetectRecord.model_version ]]</t-descriptions-item>
                <t-descriptions-item label="目标数量">[[ quickDetectRecord.detection_count ]]</t-descriptions-item>
                <t-descriptions-item label="最高置信度">[[ formatDetectConfidence(quickDetectRecord.max_confidence) ]]</t-descriptions-item>
                <t-descriptions-item label="图片标识">[[ quickDetectRecord.image_identifier || '未提供' ]]</t-descriptions-item>
                <t-descriptions-item label="识别时间" :span="3">[[ quickDetectRecord.recognized_at ]]</t-descriptions-item>
            </t-descriptions>
            <div class="hardware-detect-result">
                <div class="hardware-detect-image-pane">
                    <t-image-viewer :images="[quickDetectRecord.image_url]">
                        <template #trigger="{ open }">
                            <div class="hardware-detect-image-stage" @click="open">
                                <img :src="quickDetectRecord.image_url" alt="快捷检测原图"
                                    class="hardware-detect-image">
                                <div v-for="box in quickDetectBoxes" :key="box.key"
                                    class="hardware-detect-box" :style="box.style">
                                    <span>[[ box.label ]] [[ formatDetectConfidence(box.confidence) ]]</span>
                                </div>
                            </div>
                        </template>
                    </t-image-viewer>
                </div>
                <div class="hardware-detect-detail-pane">
                    <div v-if="quickDetectRows.length" class="hardware-detect-detail-list">
                        <section v-for="row in quickDetectRows" :key="row._rowKey"
                            class="hardware-detect-detail-item">
                            <div class="hardware-detect-detail-field">
                                <span>目标</span>
                                <strong>[[ row.label ]]</strong>
                            </div>
                            <div class="hardware-detect-detail-field">
                                <span>置信度</span>
                                <strong>[[ formatDetectConfidence(row.confidence) ]]</strong>
                            </div>
                            <div class="hardware-detect-detail-field">
                                <span>坐标 [x1, y1, x2, y2]</span>
                                <strong class="hardware-detect-coordinate">[[ formatDetectBbox(row.bbox) ]]</strong>
                            </div>
                        </section>
                    </div>
                    <div v-else class="hardware-detect-empty">
                        当前图片未识别到目标
                    </div>
                </div>
            </div>
        </template>
    </t-dialog>

    <t-dialog v-model:visible="deleteVisible" header="删除设备" theme="danger" width="560px"
        :close-on-overlay-click="false" :close-btn="!deleting" :close-on-esc-keydown="!deleting"
        :confirm-btn="{ content: '确认删除', theme: 'danger', loading: deleting }"
        :cancel-btn="{ content: '取消', disabled: deleting }" @confirm="submitDelete">
        <t-space v-if="deleteTarget" direction="vertical" class="hardware-delete-content">
            <span>确定删除设备“[[ cardName(deleteTarget) ]]”？</span>
            <span>设备 ID：[[ deleteTarget.provider_device_id ]]</span>
            <t-alert theme="warning" message="将删除注册信息、设备镜像和实时数据，操作不可撤销。硬件需重新注册后才能再次上报。"></t-alert>
            <t-alert v-if="deleteError" theme="error" :message="deleteError"></t-alert>
        </t-space>
    </t-dialog>

    <t-dialog v-model:visible="registerVisible" class="hardware-register-dialog" header="注册慧眼设备" width="880px"
        placement="center" :close-on-overlay-click="false" :close-btn="!registering" :close-on-esc-keydown="!registering"
        :confirm-btn="{ content: '提交注册', loading: registering }" :cancel-btn="{ content: '取消', disabled: registering }"
        @confirm="registerFormRef.submit()">
        <t-form ref="registerFormRef" :data="registerForm" :rules="registerRules" label-width="100px" @submit="submitRegistration">
            <t-form-item label="设备名称" name="device_name"><t-input v-model="registerForm.device_name" placeholder="请输入设备名称" :maxlength="128"></t-input></t-form-item>
            <t-form-item label="设备 ID"><t-input value="注册成功后自动生成" disabled></t-input></t-form-item>
            <t-form-item label="设备类型"><t-select v-model="registerForm.device_type" :options="deviceTypeOptions" clearable placeholder="请选择设备类型"></t-select></t-form-item>
            <t-form-item label="显示名称"><t-input v-model="registerForm.nickname" placeholder="留空使用设备名称" :maxlength="128"></t-input></t-form-item>
            <t-form-item label="安装地址"><t-textarea v-model="registerForm.address" placeholder="可填写设备安装位置，如一号温室东侧" :maxlength="256" :autosize="{ minRows: 3, maxRows: 3 }"></t-textarea></t-form-item>
            <t-alert v-if="registerError" theme="warning" :message="registerError"></t-alert>
        </t-form>
    </t-dialog>
`;
