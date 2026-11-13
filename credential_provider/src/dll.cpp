// COM plumbing: class factory and the two exports LogonUI needs.
// There is deliberately no DllRegisterServer: registration is done by
// install.ps1 so every registry change is visible and reviewable.
#include <windows.h>
#include <initguid.h>  // must come before guid.h so DEFINE_GUID emits the definition

#include <new>

#include "dll.h"
#include "guid.h"
#include "provider.h"

namespace {

LONG g_objects = 0;
LONG g_locks = 0;

class ClassFactory final : public IClassFactory {
public:
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return 2; }  // static instance
    IFACEMETHODIMP_(ULONG) Release() override { return 1; }

    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        *ppv = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* p = new (std::nothrow) fu::Provider();
        if (!p) return E_OUTOFMEMORY;
        HRESULT hr = p->QueryInterface(riid, ppv);
        p->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        lock ? InterlockedIncrement(&g_locks) : InterlockedDecrement(&g_locks);
        return S_OK;
    }
};

ClassFactory g_factory;

}  // namespace

void DllAddRef() { InterlockedIncrement(&g_objects); }
void DllRelease() { InterlockedDecrement(&g_objects); }

STDAPI DllCanUnloadNow() { return (g_objects == 0 && g_locks == 0) ? S_OK : S_FALSE; }

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    *ppv = nullptr;
    if (clsid != CLSID_FaceUnlockProvider) return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, ppv);
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(inst);
    return TRUE;
}
