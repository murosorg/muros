<!doctype html>
<html lang="{{ langcode|safe }}" class="no-js">
  <head>

    <meta charset="UTF-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge">

    <meta name="robots" content="noindex, nofollow" />
    <meta name="keywords" content="" />
    <meta name="description" content="" />
    <meta name="copyright" content="" />
    <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1" />
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">

    <title>{{headTitle|default("OPNsense") }} | {{system_hostname}}.{{system_domain}}</title>
    {% set theme_name = ui_theme|default('opnsense') %}

    <!-- Favicon -->
    <link href="{{ cache_safe('/ui/themes/%s/build/images/favicon.png' | format(theme_name)) }}" rel="shortcut icon">

    <!-- css imports -->
    {% for filename in css_files -%}
    <link href="{{ cache_safe(theme_file_or_default(filename, theme_name)) }}" rel="stylesheet">
    {% endfor %}

    <!-- TODO: move to theme style -->
    <style>
      .menu-level-3-item {
        font-size: 90%;
        padding-left: 54px !important;
      }
      .typeahead {
        overflow: hidden;
      }
    </style>

    <!-- script imports -->
    {% for filename in javascript_files -%}
    <script src="{{ cache_safe(filename) }}"></script>
    {% endfor %}

    <script>
            // setup default scripting after page loading.
            $( document ).ready(function() {
                // hook into jquery ajax requests to ensure csrf handling.
                $.ajaxSetup({
                    'beforeSend': function(xhr) {
                        xhr.setRequestHeader("X-CSRFToken", "{{ csrf_token }}" );
                    }
                });
                // propagate ajax error messages
                $( document ).ajaxError(function( event, request, ajaxSettings ) {
                    const filter_uris = ['/api/core/firmware/upgradestatus'];
                    if (request.responseJSON != undefined && request.responseJSON.errorMessage != undefined) {
                        const url = new URL(ajaxSettings.url, window.location.origin);
                        if (filter_uris.includes(url.pathname)) {
                            return; // prevent errors on specific endpoints, specified above
                        } else if ($("#opnsense-generic-error-dialog").is(':visible')) {
                            return; // prevent error windows from constantly popping up.
                        }
                        BootstrapDialog.show({
                            id: 'opnsense-generic-error-dialog',
                            type: BootstrapDialog.TYPE_DANGER,
                            title: request.responseJSON.errorTitle,
                            message:request.responseJSON.errorMessage,
                            buttons: [{
                                label: '{{ lang._('Close') }}',
                                action: function(dialogItself){
                                    dialogItself.close();
                                }
                            }]
                        });
                    }
                });

                // hide empty menu items
                $('#mainmenu > div > .collapse').each(function () {
                    // cleanup empty second level menu containers
                    $(this).find("div.collapse").each(function () {
                        if ($(this).children().length == 0) {
                            $("#mainmenu").find('[href="#' + $(this).attr('id') + '"]').remove();
                            $(this).remove();
                        }
                    });

                    // cleanup empty first level menu items
                    if ($(this).children().length == 0) {
                        $("#mainmenu").find('[href="#' + $(this).attr('id') + '"]').remove();
                    }
                });
                // hide submenu items
                $('#mainmenu .list-group-item').click(function(){
                    if($(this).attr('href').substring(0,1) == '#') {
                        $('#mainmenu .list-group-item').each(function(){
                            if ($(this).attr('aria-expanded') == 'true'  && $(this).data('parent') != '#mainmenu') {
                                $("#"+$(this).attr('href').substring(1,999)).collapse('hide');
                            }
                        });
                    }
                });

                initFormHelpUI();
                initFormAdvancedUI();
                addMultiSelectClearUI();
                initGlobalOpenShortcuts();


                // Register collapsible table headers
                $('.table').on('click', 'thead', function(event) {
                    let collapse = $(event.currentTarget).next();
                    let id = collapse.attr('class');
                    if (collapse != undefined && id !== undefined && id === "collapsible") {
                        let icon = $('> tr > th > div > i', event.currentTarget);
                        if (collapse.is(':hidden')) {
                            collapse.toggle(0);
                            collapse.css('display', '');
                            icon.toggleClass("fa-angle-right fa-angle-down");
                            return;
                        }
                        icon.toggleClass("fa-angle-down fa-angle-right");
                        $('> tr > td', collapse).toggle(0);
                    }
                });

                // enable bootstrap tooltips
                $('body').tooltip({
                    selector: '[data-toggle="tooltip"]',
                    container: 'body'
                });

                // fix menu scroll position on page load
                $(".list-group-item.active").each(function(){
                    var navbar_center = ($( window ).height() - $(".collapse.navbar-collapse").height())/2;
                    $('html,aside').scrollTop(($(this).offset().top - navbar_center));
                });
                // prevent form submits on mvc pages
                $("form").submit(function() {
                    return false;
                });

                /* overwrite clipboard paste behavior and trim before paste */
                $("input").on('paste', function(e) {
                    let clipboard_data = e.originalEvent.clipboardData.getData("text/plain").trim();
                    if (clipboard_data.length > 0) {
                        e.preventDefault();
                        document.execCommand('insertText', false, clipboard_data);
                    }
                });

            });
        </script>

        <!-- theme JS -->
        <script src="{{ cache_safe(theme_file_or_default('/js/theme.js', theme_name)) }}"></script>
  </head>
  <body>

  <main class="page-content col-sm-9 col-sm-push-3 col-lg-10 col-lg-push-2">
      <!-- menu system -->
      {{ partial("layout_partials/base_menu_system") }}
      <div class="row">
        <!-- page header -->
        <header class="page-content-head">
          <div class="container-fluid">
            <ul class="list-inline">
              <li><h1>{{title | default("")}}</h1></li>
              <li class="btn-group-container" id="service_status_container"></li>
            </ul>
          </div>
        </header>

        <!-- page content -->
        <section class="page-content-main">
          <div class="container-fluid">
            <div class="row">
                <!-- notification banner dynamically inserted here (opnsense_status.js) -->

                <section class="col-xs-12">
                    <div id="messageregion"></div>
                        {{ content() }}
                </section>
            </div>
          </div>
        </section>
        <!-- page footer -->
        <footer class="page-foot">
          <div class="container-fluid">
            <a target="_blank" href="{{ product_website }}">{{ product_name }}</a> (c) {{ product_copyright_years }}
            <a target="_blank" href="{{ product_copyright_url }}">{{ product_copyright_owner }}</a>
          </div>
        </footer>
      </div>
    </main>

    <!-- dialog "wait for (service) action" -->
    <div class="modal fade" id="OPNsenseStdWaitDialog" tabindex="-1" data-backdrop="static" data-keyboard="false">
      <div class="modal-backdrop fade in"></div>
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <p><strong>{{ lang._('Please wait...') }}</strong></p>
            <div class="progress">
               <div class="progress-bar progress-bar-info progress-bar-striped active" role="progressbar" aria-valuenow="100" aria-valuemin="0" aria-valuemax="100" style="width:100%"></div>
             </div>
          </div>
        </div>
      </div>
    </div>

    <script>
        /* hook translations  when all JS modules are loaded*/
        $.extend(jQuery.fn.UIBootgrid.translations, {
            add: "{{ lang._('Add') }}",
            deleteSelected: "{{ lang._('Delete selected') }}",
            enableSelected: "{{ lang._('Enable selected') }}",
            disableSelected: "{{ lang._('Disable selected') }}",
            edit: "{{ lang._('Edit') }}",
            disable: "{{ lang._('Disable') }}",
            enable: "{{ lang._('Enable') }}",
            delete: "{{ lang._('Delete') }}",
            info: "{{ lang._('Info') }}",
            clone: "{{ lang._('Clone') }}",
            all: "{{ lang._('All') }}",
            search: "{{ lang._('Search') }}",
            removeWarning: "{{ lang._('Remove selected item(s)?') }}",
            noresultsfound: "{{ lang._('No results found') }}",
            refresh: "{{ lang._('Refresh') }}",
            infosTotal: "{{ lang._('Showing %s to %s of %s entries') | format('{{ctx.start}}','{{ctx.end}}','{{ctx.totalRows}}') }}",
            infos: "{{ lang._('Showing %s to %s') | format('{{ctx.start}}','{{ctx.end}}') }}",
            resetGrid: "{{ lang._('Reset grid layout') }}",
            searchColumns: "{{ lang._('Search columns') }}",
            expand: "{{ lang._('Click to expand/collapse cell') }}"
        });

        $.fn.selectpicker.defaults = $.fn.selectpicker.defaults || {};
        $.extend($.fn.selectpicker.defaults, {noneSelectedText: '{{ lang._('Nothing selected') }}'});
    </script>

  </body>
</html>
