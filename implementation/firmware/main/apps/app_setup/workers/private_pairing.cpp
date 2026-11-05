#include "workers.h"
#include <hal/utils/private_pairing/private_pairing.h>
using namespace setup_workers;
using namespace uitk::lvgl_cpp;

PrivatePairingWorker::PrivatePairingWorker() {
    _panel=std::make_unique<Container>(lv_screen_active());
    _panel->setSize(320,240); _panel->setBgColor(lv_color_hex(0xEDF4FF));
    _panel->setBorderWidth(0); _panel->setRadius(0);
    _title=std::make_unique<Label>(_panel->get());
    _title->setTextFont(&lv_font_montserrat_20);
    _title->align(LV_ALIGN_TOP_MID,0,14); _title->setText("PRIVATE PAIRING");
    _code=std::make_unique<Label>(_panel->get());
    _code->setTextFont(&lv_font_montserrat_24);
    _code->align(LV_ALIGN_TOP_MID,0,52); _code->setText("------");
    _status=std::make_unique<Label>(_panel->get());
    _status->setWidth(290); _status->setTextFont(&lv_font_montserrat_14);
    _status->setTextAlign(LV_TEXT_ALIGN_CENTER);
    _status->align(LV_ALIGN_TOP_MID,0,92);
    _status->setText("Start after secure provisioning.\nConfirm the code on your phone.");
    _begin=std::make_unique<Button>(_panel->get());
    apply_button_common_style(*_begin); _begin->setSize(280,40);
    _begin->align(LV_ALIGN_TOP_MID,0,145); _begin->label().setText("Show code");
    _begin->onClick().connect([this]() { _begin_clicked=true; });
    _back=std::make_unique<Button>(_panel->get());
    apply_button_common_style(*_back); _back->setSize(280,40);
    _back->align(LV_ALIGN_TOP_MID,0,190); _back->label().setText("Back");
    _back->onClick().connect([this]() { _back_clicked=true; });
}
void PrivatePairingWorker::update() {
    if (_back_clicked) {
        if (!_claim_id.empty()) {
            GetHAL().lvglUnlock(); private_pairing::cancel(_claim_id); GetHAL().lvglLock();
        }
        _code->setText("------"); _is_done=true; return;
    }
    if (_begin_clicked) {
        _begin_clicked=false;
        GetHAL().lvglUnlock();
        if (!_claim_id.empty()) private_pairing::cancel(_claim_id);
        const auto result=private_pairing::start();
        GetHAL().lvglLock();
        _claim_id.clear(); _code->setText("------");
        if (!result.ok) { _status->setText("Pairing unavailable.\nCheck secure session and Wi-Fi."); return; }
        _claim_id=result.claim_id; _code->setText(result.code);
        _expires=GetHAL().millis()+result.expires_in*1000;
        _next_poll=GetHAL().millis()+2000;
        _status->setText("Enter this code on your phone.\nValid for two minutes.");
    }
    if (_claim_id.empty()) return;
    const uint32_t tick=GetHAL().millis();
    if (static_cast<int32_t>(tick-_expires)>=0) {
        _claim_id.clear(); _code->setText("------"); _status->setText("Code expired. Tap Show code."); return;
    }
    if (static_cast<int32_t>(tick-_next_poll)<0) return;
    _next_poll=tick+2000;
    GetHAL().lvglUnlock(); const auto result=private_pairing::poll(_claim_id); GetHAL().lvglLock();
    if (result.ok && result.bound) {
        _claim_id.clear(); _code->setText("PAIRED");
        _status->setText("Phone confirmed.\nTemporary private session active.");
        _begin->addState(LV_STATE_DISABLED);
    } else if (!result.ok) {
        _claim_id.clear(); _code->setText("------");
        _status->setText("Session ended. Check connection\nand request a new code.");
    }
}
