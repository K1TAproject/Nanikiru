"""Build the pinned local akochan checkout with installed MSVC and Boost headers."""
import argparse
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,default=Path('data/external-bots/akochan'))
    p.add_argument('--boost',type=Path,default=Path('data/external-bots/boost_1_70_0'))
    p.add_argument('--vcvars',default=r'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat')
    p.add_argument('--asan',action='store_true',help='Separate address-sanitizer diagnostic build')
    p.add_argument('--threads',type=int,default=4,help='akochan upstream Windows default; also sizes per-thread work buffers')
    args=p.parse_args();root=args.source.resolve();build=root/('nanikiru-asan' if args.asan else 'nanikiru-build');build.mkdir(exist_ok=True)
    if args.threads<1:p.error('--threads must be positive')
    # Upstream declares these mutable globals const in other translation units.
    # GCC links them anyway; MSVC correctly distinguishes their symbol types.
    for header in (root/'ai_src').glob('*.cpp'):
        source=header.read_text(encoding='utf-8-sig')
        fixed=source.replace('extern const bool out_console','extern bool out_console').replace(
            'extern const std::array<json11::Json, 4> tactics_json','extern std::array<json11::Json, 4> tactics_json')
        if header.name=='tehai_ana.cpp':
            # Bounds must be checked before evaluating hai+1/hai+2 for honors.
            fixed=fixed.replace('tehai_tmp[hai] && tehai_tmp[hai+1] && hai<30',
                                'hai<30 && tehai_tmp[hai] && tehai_tmp[hai+1]').replace(
                'tehai_tmp[hai] && tehai_tmp[hai+2] && hai<30 && hai%10<=8',
                'hai<30 && hai%10<=8 && tehai_tmp[hai] && tehai_tmp[hai+2]')
        if fixed!=source:header.write_text(fixed,encoding='utf-8')
    sources=sorted((root/'ai_src').glob('*.cpp'))+sorted((root/'ai_src/learn').glob('*.cpp'))+sorted((root/'share').glob('*.cpp'))
    sources.append(Path(__file__).with_name('akochan_bridge.cpp').resolve())
    flags=['/nologo','/O2','/EHsc','/std:c++14','/openmp','/utf-8','/bigobj',f'/DNPROCS={args.threads}','/DWINSTD',
           '/DNOMINMAX','/DBOOST_ALL_NO_LIB','/D_CRT_SECURE_NO_WARNINGS',f'/I"{root}"',f'/I"{args.boost.resolve()}"']
    if args.asan:flags+=['/fsanitize=address','/Zi']
    # Separate object files are needed: upstream directories share filenames.
    commands=[f'call "{args.vcvars}"']
    objects=[]
    for i,source in enumerate(sources):
        obj=build/f'{i}.obj';objects.append(f'"{obj}"')
        commands += ['cl '+' '.join(flags)+f' /c "{source}" /Fo"{obj}"','if errorlevel 1 exit /b 1']
    commands += ['link /NOLOGO /STACK:16777216 '+('/DEBUG ' if args.asan else '')+ ' '.join(objects)+f' ws2_32.lib /OUT:"{build / "akochan.exe"}"']
    script=build/'build.cmd';script.write_text('\n'.join(commands),encoding='utf-8')
    subprocess.run(['cmd','/c',str(script)],cwd=build,check=True)


if __name__=='__main__':main()
